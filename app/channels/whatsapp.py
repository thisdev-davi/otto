import asyncio
import base64
import hmac
import ipaddress
import json
import logging
import secrets
from datetime import datetime, timezone
from urllib.parse import urlsplit
import httpx
from app.channels.base import ChannelProvider
from app.channels.types import (
    ChannelStatus,
    NormalizedInbound,
    NormalizedMessageChange,
    NormalizedStatusUpdate,
    OutboundMessage,
    SendResult,
)
from app.core.config import settings
from app.models.enums import Channel, ContentType, MessageStatus

# Import de serviço dentro de `channels/` é o sentido PERMITIDO da regra 1 do CLAUDE.md
# (o núcleo é que não pode importar provider concreto). `attachments` só conhece config e
# enums, então não há ciclo — e é lá que mora a tabela única de tetos por mimetype.
from app.services import attachments

log = logging.getLogger("uvicorn.error")

# Chave do payload Evolution → (ContentType, campo que vira content da Message)
_MEDIA_TYPES = {
    "imageMessage": (ContentType.IMAGE, "caption"),
    "videoMessage": (ContentType.VIDEO, "caption"),
    "audioMessage": (ContentType.AUDIO, None),
    "documentMessage": (ContentType.DOCUMENT, "fileName"),
    "stickerMessage": (ContentType.STICKER, None),
}

# Chaves de `message` que NÃO são mensagem do cliente: nada aqui vira linha na thread.
# `reactionMessage` é o caso que dói — reagir a uma bolha do bot é gesto comum e
# disparava um turno inteiro. `protocolMessage` cobre apagar/editar; `pollUpdateMessage`,
# voto em enquete.
_NAO_E_MENSAGEM = ("reactionMessage", "protocolMessage", "pollUpdateMessage")

# ContentType do Otto → `mediatype` do Evolution, no ENVIO. Mora aqui, e não no schema
# nem na rota, porque "mediatype" é vocabulário do Evolution: trocar de provider não
# pode obrigar a mexer em app/api. STICKER fica de fora — o Evolution usa um endpoint
# próprio (/message/sendSticker) e o upload não aceita esse tipo.
_OUTBOUND_MEDIATYPE = {
    ContentType.IMAGE: "image",
    ContentType.VIDEO: "video",
    ContentType.AUDIO: "audio",
    ContentType.DOCUMENT: "document",
}

# Teto de mensagens por chamada de markMessageAsRead. Abrir uma conversa antiga com
# centenas de não-lidas mandaria a thread inteira num payload só; as mais recentes são
# as que importam para o tique, e o WhatsApp trata leitura como cumulativa de fato.
_MAX_MARCAR_LIDAS = 50

# Quanto o "digitando…" fica no ar do lado do cliente, em ms. Curto de propósito — ver
# `send_presence`.
_PRESENCA_MS = 3000

# ACK do Evolution (messages.update) → status normalizado do Otto.
_STATUS_MAP = {
    "DELIVERY_ACK": MessageStatus.DELIVERED,
    "READ": MessageStatus.READ,
    "PLAYED": MessageStatus.READ,
    "SERVER_ACK": MessageStatus.SENT,
    "ERROR": MessageStatus.FAILED,
}


# Nome de perfil do WhatsApp é texto livre escolhido por quem manda a mensagem, e ele
# entra no contexto do agente todo turno (`contato_ja_identificado_nome`) e no resumo da
# pré-reserva que a equipe lê. Um "nome" de 400 caracteres com quebras de linha é o
# formato clássico de injeção de prompt — cortar aqui, na fronteira, vale mais do que
# confiar no modelo lá na frente.
_MAX_PUSH_NAME = 60


def _limpa_push_name(bruto) -> str | None:
    if not isinstance(bruto, str):
        return None
    # Quebras de linha e caracteres de controle saem: são o que permite forjar uma linha
    # nova dentro do JSON de contexto e fingir ser outro campo.
    limpo = "".join(c for c in bruto if c.isprintable()).strip()
    return limpo[:_MAX_PUSH_NAME] or None


# Telefone brasileiro tem 12-13 dígitos com DDI; o teto cobre os maiores planos de
# numeração do mundo. Fora da faixa, é mais seguro dizer "não sei" do que gravar lixo no
# campo que o dono do salão usa para ligar para o cliente.
_MIN_DIGITOS_TELEFONE = 10
_MAX_DIGITOS_TELEFONE = 15


def _telefone(key: dict) -> str | None:
    """Telefone do remetente a partir da `key` do Evolution. `None` quando não dá para saber.

    O `remoteJid` NEM SEMPRE é um telefone. O WhatsApp passou a entregar remetente como
    `<numero>@lid` — identificador opaco, estável só dentro da conexão, que não é o
    telefone de ninguém. Quando isso acontece, o número real vem em `senderPn`.

    Ler o `remoteJid` como se fosse telefone é o que gravou `21711730823330@lid` no campo
    telefone de uma reserva confirmada — o dono do salão não tinha como ligar para o
    cliente, e ninguém percebeu porque o campo estava preenchido.

    Este é o ÚNICO lugar do sistema que sabe o que `@lid` significa (regra 1 do CLAUDE.md):
    o núcleo recebe dígitos ou `None`, e não precisa aprender formato de JID.
    """
    for campo in ("senderPn", "remoteJid"):
        bruto = key.get(campo)
        if not isinstance(bruto, str):
            continue
        # `@lid` não carrega telefone nenhum — pular para o próximo candidato em vez de
        # extrair os dígitos do identificador opaco, que é exatamente o bug original.
        if bruto.endswith("@lid"):
            continue
        # Corta o domínio e o sufixo `:device` que o multi-dispositivo acrescenta.
        digitos = "".join(c for c in bruto.split("@")[0].split(":")[0] if c.isdigit())
        if _MIN_DIGITOS_TELEFONE <= len(digitos) <= _MAX_DIGITOS_TELEFONE:
            return digitos
    return None


# Motivo de recusa quando o endereço é um lid e não conhecemos o telefone por trás dele.
# Vai gravado em `messages.channel_metadata.erro` e é o que o atendente lê na Inbox.
ERRO_LID_SEM_TELEFONE = (
    "endereço @lid sem telefone conhecido: o WhatsApp não entrega nesse identificador"
)


def _sem_destino(message: OutboundMessage) -> str | None:
    """Por que esta mensagem não tem para onde ir — `None` quando tem.

    Existe para o envio FALHAR em vez de fingir. Um `@lid` sem telefone é entregue ao
    Evolution sem erro nenhum: ele devolve 201 com uma key de verdade e a Inbox marca
    `SENT`, mas o ACK que volta depois é `ERROR` e o cliente nunca recebe nada. Recusar
    aqui é o que aciona `_desviar_por_falha_de_envio`, e aí a conversa vai para a fila da
    equipe com nota em vez de morrer calada.

    O caso real é o contato `@lid` antigo, criado antes de guardarmos `senderPn`: a
    migration `b6e29a0c4d17` não tem de onde tirar o número dele, e só a próxima mensagem
    que ele mandar cura o cadastro.
    """
    if message.to_external_id.endswith("@lid") and not message.to_phone:
        return ERRO_LID_SEM_TELEFONE
    return None


def _destino(message: OutboundMessage) -> str:
    """Para onde o Evolution deve mandar: o endereço, ou o telefone quando ele não roteia.

    `@lid` NÃO É ENTREGÁVEL. O mesmo identificador opaco que `_telefone` já sabia não ser
    telefone também não é destino: `/chat/whatsappNumbers` responde `exists: false` para
    ele. O Evolution aceita mesmo assim — devolve 201 com uma key de mensagem de verdade,
    e a mídia até sobe para o servidor do WhatsApp — mas o ACK que chega depois é `ERROR`
    e o aparelho do cliente nunca recebe nada. O sintoma é o pior possível: o "digitando…"
    aparece (a presença ROTEIA para o lid), a Inbox marca SENT, e a mensagem não existe do
    outro lado.

    O telefone real vem em `senderPn` e já era guardado ao lado do endereço justamente
    porque `remoteJid` deixou de ser telefone. Aqui ele fecha o par: entra como destino
    quando o endereço é um lid.

    Fora do lid, o endereço continua mandando — inclusive o `@s.whatsapp.net`, que roteia
    e é o que existe nas conversas antigas.

    Lid sem telefone não chega aqui: `send` recusa antes, por `_sem_destino`.
    """
    if message.to_external_id.endswith("@lid") and message.to_phone:
        return message.to_phone
    return message.to_external_id


def _timestamp(bruto) -> datetime:
    """`messageTimestamp` do payload → datetime UTC, sem derrubar a task.

    Vinha como `int(bruto)` direto: um valor não numérico ou absurdamente grande levantava
    ValueError/OverflowError dentro do worker e matava a ingestão do lote inteiro.
    """
    try:
        return datetime.fromtimestamp(int(bruto), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return datetime.now(timezone.utc)


# A foto de perfil é imagem e nada além disso. Subconjunto da allow-list de anexos, e não
# ela inteira: aqui não existe caso de uso para PDF nem para vídeo.
_AVATAR_ACEITOS = frozenset({"image/jpeg", "image/png", "image/webp"})


async def _url_publica(url: str) -> bool:
    """A URL da foto vem do Evolution, não de nós: só HTTPS para IP público.

    Sem isto, um Evolution comprometido (ou uma resposta forjada) apontava o `GET` para
    `169.254.169.254`, `localhost` ou a rede interna — SSRF a partir do worker. O redirect
    fica desligado pelo mesmo motivo: o destino final também teria de passar por aqui.
    """
    # ponytail: checa o DNS antes do GET, então um rebinding entre as duas resoluções
    # passa; fixar o IP resolvido no transporte do httpx fecha isso se um dia importar.
    partes = urlsplit(url)
    if partes.scheme != "https" or not partes.hostname:
        return False
    try:
        enderecos = await asyncio.get_running_loop().getaddrinfo(
            partes.hostname, partes.port or 443
        )
    except OSError:
        return False
    return bool(enderecos) and all(
        ipaddress.ip_address(info[4][0]).is_global for info in enderecos
    )


class EvolutionProvider(ChannelProvider):
    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        """Confere a apikey compartilhada — a PRIMEIRA de duas barreiras.

        Esta prova apenas "veio do nosso Evolution". Ela NÃO diz de qual tenant é o
        evento: quem diz isso é o `webhook_id` no caminho da URL, resolvido em
        `app/api/webhooks.py`. A separação é o ponto — antes, esta única chave
        compartilhada era o portão inteiro, e o tenant vinha de um campo do corpo
        escolhido por quem enviava.

        `compare_digest` porque `==` em str sai no primeiro byte diferente.
        """
        return hmac.compare_digest(
            str(headers.get("apikey", "")), settings.EVOLUTION_API_KEY
        )

    def parse_webhook(
        self, headers: dict, body: bytes
    ) -> list[NormalizedInbound | NormalizedStatusUpdate]:
        payload = json.loads(body)
        event = payload.get("event")
        if event == "messages.update":
            return self._parse_status(payload)
        if event in ("messages.delete", "messages.edited"):
            return self._parse_revisao(event, payload.get("data") or {})
        if event != "messages.upsert":
            return []

        data = payload.get("data", {})
        key = data.get("key", {})

        if key.get("fromMe"):
            return []

        remote_jid: str = key.get("remoteJid", "")

        if remote_jid.endswith("@g.us"):
            return []
        external_id: str = key.get("id", "")
        instance_key: str = payload.get("instance", "")
        push_name: str | None = _limpa_push_name(data.get("pushName"))
        message_obj: dict = data.get("message", {})
        timestamp = _timestamp(data.get("messageTimestamp", 0))

        if any(k in message_obj for k in _NAO_E_MENSAGEM):
            # Reação, edição/apagamento e voto em enquete chegam pelo mesmo evento de
            # mensagem nova. Cair no genérico de `_parse_media` fazia um 👍 do cliente
            # virar linha na thread e disparar resposta do bot: o cliente reagia à bolha
            # e o bot respondia "recebi seu arquivo".
            return []

        content = message_obj.get("conversation") or message_obj.get(
            "extendedTextMessage", {}
        ).get("text")
        channel_metadata: dict = {"raw": payload}

        if content:
            content_type = ContentType.TEXT
        else:
            content_type, content = self._parse_media(message_obj, channel_metadata)

        return [
            NormalizedInbound(
                channel=Channel.WHATSAPP,
                instance_key=instance_key,
                external_id=external_id,
                # O endereço de resposta continua sendo o `remoteJid` cru: é por ele que
                # o envio funciona hoje. O telefone vai AO LADO, não no lugar.
                contact_external_id=remote_jid,
                contact_phone=_telefone(key),
                contact_name=push_name,
                content_type=content_type,
                content=content,
                channel_metadata=channel_metadata,
                timestamp=timestamp,
            )
        ]

    @staticmethod
    def _parse_status(payload: dict) -> list[NormalizedStatusUpdate]:
        """messages.update traz o ACK de entrega/leitura de uma mensagem que enviamos.

        **O payload do Evolution é PLANO.** Ele monta o evento como
        `{messageId, keyId, remoteJid, fromMe, participant, status, instanceId}` e manda
        assim; não existe `key` aninhado aqui, ao contrário do `messages.upsert`. Ler só
        `data["key"]["id"]` fazia `external_id` sair vazio e o evento ser descartado em
        silêncio — TODO ACK, incluindo os `ERROR`. O efeito era pior que perder o ✓✓: a
        Inbox marcava `SENT` no HTTP 201 do Evolution e nunca mais corrigia, então uma
        mensagem que o WhatsApp recusou aparecia para o atendente como entregue. Foi
        assim que o envio para `@lid` ficou seis eventos de `ERROR` invisíveis.

        Os dois formatos são aceitos porque o aninhado é o que o `CloudAPIProvider`
        usaria, e distinguir provedor aqui não paga.
        """
        data = payload.get("data", {})
        key = data.get("key") or {}
        external_id = key.get("id") or data.get("keyId") or ""
        raw_status = data.get("status") or data.get("update", {}).get("status")
        status = _STATUS_MAP.get(str(raw_status))
        if not external_id or status is None:
            return []
        return [
            NormalizedStatusUpdate(
                channel=Channel.WHATSAPP, external_id=external_id, status=status
            )
        ]

    @staticmethod
    def _parse_revisao(event: str, data: dict) -> list[NormalizedMessageChange]:
        """Apagar para todos e editar, vindos do aparelho do cliente (ou do nosso).

        Os dois formatos foram conferidos no Evolution 2.2.3, não presumidos:
        `messages.delete` manda a `key` crua (`{remoteJid, fromMe, id}`) como `data`;
        `messages.edited` manda o `protocolMessage` (`{key, editedMessage, ...}`), com o
        texto novo dentro de `editedMessage`.
        """
        if event == "messages.delete":
            external_id = data.get("id") or (data.get("key") or {}).get("id")
            if not external_id:
                return []
            return [
                NormalizedMessageChange(
                    channel=Channel.WHATSAPP, external_id=external_id, acao="apagada"
                )
            ]
        external_id = (data.get("key") or {}).get("id")
        editada = data.get("editedMessage") or {}
        texto = (
            editada.get("conversation")
            or (editada.get("extendedTextMessage") or {}).get("text")
            or (editada.get("imageMessage") or {}).get("caption")
            or (editada.get("videoMessage") or {}).get("caption")
        )
        if not external_id or not texto:
            return []
        return [
            NormalizedMessageChange(
                channel=Channel.WHATSAPP,
                external_id=external_id,
                acao="editada",
                content=texto,
            )
        ]

    @staticmethod
    def _parse_media(
        message_obj: dict, channel_metadata: dict
    ) -> tuple[ContentType, str | None]:
        """Classifica mensagem sem texto e marca a mídia para download posterior."""
        for key, (content_type, content_field) in _MEDIA_TYPES.items():
            media_obj = message_obj.get(key)
            if not media_obj:
                continue
            channel_metadata["media"] = {
                "has_media": True,
                "mimetype": media_obj.get("mimetype"),
                "status": "pending",
            }
            content = media_obj.get(content_field) if content_field else None
            return content_type, content
        # sem texto e sem mídia conhecida (reação, localização...) — mantém o raw
        return ContentType.DOCUMENT, None

    async def fetch_media(
        self, credentials: dict, message_metadata: dict
    ) -> tuple[bytes, str] | None:
        """Baixa a mídia via getBase64FromMediaMessage do Evolution.

        Usa o payload bruto (channel_metadata.raw) persistido na ingestão — o
        Evolution reidrata a mídia a partir da key da mensagem.
        """
        raw = message_metadata.get("raw") or {}
        data = raw.get("data") or {}
        key = data.get("key")
        instance = raw.get("instance") or credentials.get("instance")
        if not key or not instance:
            return None
        mimetype = (message_metadata.get("media") or {}).get(
            "mimetype"
        ) or "application/octet-stream"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.EVOLUTION_URL}/chat/getBase64FromMediaMessage/{instance}",
                json={"message": {"key": key}, "convertToMp4": False},
                headers={"apikey": settings.EVOLUTION_API_KEY},
                timeout=30,
            )
        if resp.status_code not in (200, 201):
            return None
        body = resp.json()
        b64 = body.get("base64")
        if not b64:
            return None
        mimetype = body.get("mimetype") or mimetype

        # Teto ANTES de decodificar. Do outro lado desta chamada está o arquivo que um
        # desconhecido mandou no WhatsApp, e daqui ele ia direto para `f.write` em
        # `tasks/media.py` — sem limite nenhum entre a rede e o disco. Os tetos por tipo
        # já existem em `services/attachments.py` para o anexo que o atendente ENVIA;
        # reusar os mesmos aqui evita duas tabelas de limite que divergem.
        teto = attachments.teto_para(mimetype)
        # base64 infla ~4/3; comparar antes de decodificar evita materializar o excesso.
        if len(b64) * 3 // 4 > teto:
            log.warning(
                "Mídia inbound descartada: ~%d bytes acima do teto de %d para %s.",
                len(b64) * 3 // 4,
                teto,
                mimetype,
            )
            return None

        try:
            dados = base64.b64decode(b64)
        except (ValueError, TypeError):
            return None
        if len(dados) > teto:
            return None
        return dados, mimetype

    async def send(self, message: OutboundMessage) -> SendResult:
        motivo = _sem_destino(message)
        if motivo is not None:
            log.warning("Envio para %s recusado: %s", message.to_external_id, motivo)
            return SendResult(success=False, error=motivo)
        if message.media is not None:
            return await self._send_media(message)
        return await self._send_text(message)

    async def _send_media(self, message: OutboundMessage) -> SendResult:
        """Anexo via /message/sendMedia.

        Base64 no corpo, e não URL pública, por três razões: `WEBHOOK_BASE_URL` é
        documentada como opcional e envio de mídia não pode depender dela; servir por
        URL exigiria uma rota de mídia SEM autenticação, superfície nova para zero
        ganho; e base64 mantém o envio atômico — com URL o Evolution busca o arquivo
        depois, então a falha aconteceria DEPOIS de já termos respondido sucesso.

        Áudio com `ptt=True` sai por /message/sendWhatsAppAudio (`_send_ptt`), que é o
        que produz o balão de nota de voz. A escolha cabe inteira dentro deste método:
        nem a rota nem o schema sabem que ela existe.
        """
        assert message.media is not None
        if message.media.ptt and message.content_type == ContentType.AUDIO:
            return await self._send_ptt(message)
        instance_key = message.channel_metadata.get("instance_key", "otto-test")
        url = f"{settings.EVOLUTION_URL}/message/sendMedia/{instance_key}"
        payload: dict = {
            "number": _destino(message),
            "mediatype": _OUTBOUND_MEDIATYPE[message.content_type],
            "mimetype": message.media.mimetype,
            "media": base64.b64encode(message.media.data).decode(),
            "fileName": message.media.filename,
        }
        # Só manda `caption` quando existe: string vazia vira legenda em branco no
        # balão do cliente em vez de nenhuma legenda.
        if message.content:
            payload["caption"] = message.content

        # 60s de base contra os 10s do texto: subir 16 MB de vídeo mais o overhead de
        # base64 não cabe no timeout de uma mensagem de texto.
        timeout = 60.0
        if message.delay_ms > 0:
            payload["delay"] = message.delay_ms
            timeout += message.delay_ms / 1000 + 15

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"apikey": settings.EVOLUTION_API_KEY},
                timeout=timeout,
            )
        if resp.status_code in (200, 201):
            return SendResult(
                success=True, external_id=resp.json().get("key", {}).get("id")
            )
        return SendResult(success=False, error=resp.text)

    async def fetch_avatar(
        self, credentials: dict, external_id: str
    ) -> tuple[bytes, str] | None:
        """Foto de perfil via /chat/fetchProfilePictureUrl, já baixada.

        Duas idas: o Evolution devolve uma URL do CDN do WhatsApp e é ela que tem os
        bytes. Contato que esconde a foto (o caso comum) responde sem `profilePictureUrl`
        — não é erro, é resposta.

        **O mimetype vem do outro lado da rede.** É a regra 6 do `CLAUDE.md` no ponto
        exato onde ela nasceu: a allow-list decide o que é aceito, e um `content-type`
        fora dela derruba a imagem em vez de virar arquivo servido na nossa origem. O
        teto de tamanho vem de `attachments`, o mesmo do anexo que sai — dois conjuntos
        de limites divergem, e o esquecido vira o buraco.
        """
        instance = credentials.get("instance")
        if not instance or not external_id:
            return None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{settings.EVOLUTION_URL}/chat/fetchProfilePictureUrl/{instance}",
                    json={"number": external_id},
                    headers={"apikey": settings.EVOLUTION_API_KEY},
                    timeout=15.0,
                )
                if resp.status_code not in (200, 201):
                    return None
                url = (resp.json() or {}).get("profilePictureUrl")
                if not url or not await _url_publica(url):
                    return None
                imagem = await client.get(url, timeout=30.0, follow_redirects=False)
        except Exception as exc:
            log.warning("fetch_avatar falhou: %s", exc)
            return None
        if imagem.status_code != 200:
            return None
        mimetype = (imagem.headers.get("content-type") or "").split(";")[0].strip()
        if mimetype not in _AVATAR_ACEITOS:
            log.warning("fetch_avatar: content-type recusado (%r)", mimetype)
            return None
        if len(imagem.content) > attachments.teto_para(mimetype):
            log.warning("fetch_avatar: imagem acima do teto para %s", mimetype)
            return None
        return imagem.content, mimetype

    async def mark_read(
        self, instance_key: str, to_external_id: str, message_ids: list[str]
    ) -> bool:
        """Tique azul via /chat/markMessageAsRead.

        `fromMe: False` em todas: só faz sentido marcar como lida a mensagem que o
        CLIENTE mandou. Marcar a nossa não significa nada e o Evolution a rejeita.

        Best-effort de propósito — devolve bool em vez de levantar. Falhar aqui não pode
        derrubar a abertura da conversa: o atendente perderia a thread por causa de um
        tique.
        """
        if not message_ids or not instance_key:
            return False
        url = f"{settings.EVOLUTION_URL}/chat/markMessageAsRead/{instance_key}"
        payload = {
            "readMessages": [
                {"remoteJid": to_external_id, "fromMe": False, "id": mid}
                for mid in message_ids[:_MAX_MARCAR_LIDAS]
            ]
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"apikey": settings.EVOLUTION_API_KEY},
                    timeout=10.0,
                )
        except Exception as exc:
            log.warning("mark_read falhou: %s", exc)
            return False
        return resp.status_code in (200, 201)

    async def send_presence(
        self, instance_key: str, to_external_id: str, presence: str = "composing"
    ) -> bool:
        """ "Digitando…" via /chat/sendPresence.

        `delay` é quanto a presença fica no ar do lado do cliente. 3s e não mais: o
        indicador expira sozinho, então um valor alto deixa "digitando" preso na tela do
        cliente depois que o atendente já desistiu de escrever — que é pior que não ter.
        O front reenvia enquanto a pessoa continua digitando.
        """
        if not instance_key:
            return False
        url = f"{settings.EVOLUTION_URL}/chat/sendPresence/{instance_key}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    json={
                        "number": to_external_id,
                        "presence": presence,
                        "delay": _PRESENCA_MS,
                    },
                    headers={"apikey": settings.EVOLUTION_API_KEY},
                    timeout=10.0,
                )
        except Exception as exc:
            log.warning("send_presence falhou: %s", exc)
            return False
        return resp.status_code in (200, 201)

    async def _send_ptt(self, message: OutboundMessage) -> SendResult:
        """Nota de voz via /message/sendWhatsAppAudio.

        Endpoint separado porque o resultado é OUTRA coisa no aparelho do cliente: o
        balão de voz com onda e velocidade de reprodução, e não um anexo com nome de
        arquivo. O `sendMedia` com mediatype=audio entrega o segundo.

        **O Evolution converte o container.** O que o navegador grava depende dele —
        Chrome dá `audio/webm;codecs=opus`, Firefox `audio/ogg;codecs=opus`, Safari
        `audio/mp4` — e o WhatsApp só aceita ogg/opus. Quem normaliza é o ffmpeg do
        Evolution, e é por isso que `fileName`/`mimetype` não vão no payload: mandar o
        container de origem aqui atrapalha a detecção dele.

        Sem `caption`: nota de voz não tem legenda no WhatsApp. Se houver texto junto,
        quem o envia é a rota, como mensagem separada.
        """
        assert message.media is not None
        instance_key = message.channel_metadata.get("instance_key", "otto-test")
        url = f"{settings.EVOLUTION_URL}/message/sendWhatsAppAudio/{instance_key}"
        payload: dict = {
            "number": _destino(message),
            "audio": base64.b64encode(message.media.data).decode(),
        }
        timeout = 60.0
        if message.delay_ms > 0:
            payload["delay"] = message.delay_ms
            timeout += message.delay_ms / 1000 + 15

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"apikey": settings.EVOLUTION_API_KEY},
                timeout=timeout,
            )
        if resp.status_code in (200, 201):
            return SendResult(
                success=True, external_id=resp.json().get("key", {}).get("id")
            )
        return SendResult(success=False, error=resp.text)

    async def _send_text(self, message: OutboundMessage) -> SendResult:
        instance_key = message.channel_metadata.get("instance_key", "otto-test")
        url = f"{settings.EVOLUTION_URL}/message/sendText/{instance_key}"
        payload: dict = {"number": _destino(message), "text": message.content}
        # `delay` é nativo do Evolution: ele exibe a presença "composing" (digitando)
        # durante a espera e só então entrega. Sai mais barato e mais convincente que
        # dormir do nosso lado, porque o cliente VÊ o indicador.
        timeout = 10.0
        if message.delay_ms > 0:
            payload["delay"] = message.delay_ms
            # O Evolution segura a resposta HTTP durante o delay — com o timeout fixo de
            # 10s, uma pausa de 4s mais latência de rede estouraria e o envio contaria
            # como falha mesmo tendo saído.
            timeout = message.delay_ms / 1000 + 15
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"apikey": settings.EVOLUTION_API_KEY},
                timeout=timeout,
            )
        if resp.status_code in (200, 201):
            return SendResult(
                success=True, external_id=resp.json().get("key", {}).get("id")
            )
        return SendResult(success=False, error=resp.text)

    def _webhook_config(self, webhook_id: str) -> dict:
        """Config do webhook que o Evolution deve usar para nos entregar eventos.

        `headers` é obrigatório e não é decoração: `verify_webhook` (acima) rejeita com
        401 toda requisição sem `apikey`. Registrar o webhook sem esse header cria uma
        contradição silenciosa — o Evolution entrega, o Otto recusa, e nada aparece na
        inbox. Sem erro em lugar nenhum: do lado do Evolution é um 401 num log que
        ninguém lê, e do lado do Otto a mensagem simplesmente nunca existiu.

        `webhook_id` vai no CAMINHO da URL, e não num header, porque é ele que identifica
        a conexão: a rota resolve tenant e instância a partir dele, em vez de acreditar no
        campo `instance` do corpo. Cada conexão tem o seu, então o segredo de um salão
        não alcança o outro.
        """
        base = settings.WEBHOOK_BASE_URL.rstrip("/")
        return {
            "enabled": True,
            "url": f"{base}/webhooks/whatsapp/{webhook_id}",
            "headers": {"apikey": settings.EVOLUTION_API_KEY},
            "byEvents": False,
            "base64": False,
            # DELETE e EDITED: o cliente apagou ou editou. Conexão criada antes disto só
            # passa a recebê-los ao reconectar (`provision` reafirma o webhook).
            "events": [
                "MESSAGES_UPSERT",
                "MESSAGES_UPDATE",
                "MESSAGES_DELETE",
                "MESSAGES_EDITED",
            ],
        }

    async def provision(self, credentials: dict) -> dict:
        instance = credentials.get("instance")
        if not instance:
            raise ValueError("Conexão WhatsApp exige 'instance' nas credenciais")

        # Preserva o `webhook_id` de uma conexão já provisionada: `provision` é chamado de
        # novo ao reconectar, e trocar o segredo aqui invalidaria a URL que o Evolution
        # já tem registrada — mensagens parariam de chegar até o próximo webhook/set.
        webhook_id = credentials.get("webhook_id") or secrets.token_urlsafe(32)

        payload: dict = {
            "instanceName": instance,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
        }
        if settings.WEBHOOK_BASE_URL:
            payload["webhook"] = self._webhook_config(webhook_id)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.EVOLUTION_URL}/instance/create",
                json=payload,
                headers={"apikey": settings.EVOLUTION_API_KEY},
                timeout=20,
            )
            # 403/409: instância já existe — provisionamento é idempotente.
            if resp.status_code not in (200, 201, 403, 409):
                raise RuntimeError(f"Falha ao criar instância Evolution: {resp.text}")

            # Reafirma o webhook SEMPRE, inclusive no 403/409. O /instance/create só
            # aplica a config quando de fato cria; para instância pré-existente ele
            # devolve "já existe" e ignora o bloco `webhook`. Sem esta chamada, toda
            # instância criada antes desta correção ficaria presa no webhook sem apikey
            # — e reconectar pelo painel não resolveria, porque reconectar não recria.
            if settings.WEBHOOK_BASE_URL:
                hook = await client.post(
                    f"{settings.EVOLUTION_URL}/webhook/set/{instance}",
                    json={"webhook": self._webhook_config(webhook_id)},
                    headers={"apikey": settings.EVOLUTION_API_KEY},
                    timeout=20,
                )
                if hook.status_code not in (200, 201):
                    raise RuntimeError(
                        f"Instância criada, mas o webhook não foi registrado "
                        f"({hook.status_code}): {hook.text}. Sem webhook nenhuma "
                        f"mensagem chega ao Otto."
                    )
        return {**credentials, "instance": instance, "webhook_id": webhook_id}

    async def get_status(self, credentials: dict) -> ChannelStatus:
        instance = credentials.get("instance")
        if not instance:
            return ChannelStatus(
                state="error", detail="instance ausente nas credenciais"
            )

        headers = {"apikey": settings.EVOLUTION_API_KEY}
        async with httpx.AsyncClient() as client:
            state_resp = await client.get(
                f"{settings.EVOLUTION_URL}/instance/connectionState/{instance}",
                headers=headers,
                timeout=10,
            )
            if state_resp.status_code == 404:
                return ChannelStatus(
                    state="disconnected", detail="instância não existe no Evolution"
                )

            state = (
                state_resp.json().get("instance", {}).get("state")
                if state_resp.status_code == 200
                else None
            )
            if state == "open":
                return ChannelStatus(state="connected")

            # Não conectado → buscar QR para parear.
            connect_resp = await client.get(
                f"{settings.EVOLUTION_URL}/instance/connect/{instance}",
                headers=headers,
                timeout=10,
            )
            if connect_resp.status_code == 200:
                data = connect_resp.json()
                qr = data.get("base64") or data.get("qrcode", {}).get("base64")
                if qr:
                    return ChannelStatus(
                        state="qr_pending", qr_code=qr, detail=data.get("pairingCode")
                    )

        return ChannelStatus(
            state="connecting" if state == "connecting" else "disconnected"
        )

    async def deprovision(self, credentials: dict) -> None:
        instance = credentials.get("instance")
        if not instance:
            return
        headers = {"apikey": settings.EVOLUTION_API_KEY}
        async with httpx.AsyncClient() as client:
            await client.delete(
                f"{settings.EVOLUTION_URL}/instance/logout/{instance}",
                headers=headers,
                timeout=10,
            )
            await client.delete(
                f"{settings.EVOLUTION_URL}/instance/delete/{instance}",
                headers=headers,
                timeout=10,
            )


class CloudAPIProvider(ChannelProvider):
    """Destino da migração Evolution → Cloud API Oficial da Meta (pós-MVP)."""

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        raise NotImplementedError

    def parse_webhook(self, headers: dict, body: bytes) -> list[NormalizedInbound]:
        raise NotImplementedError

    async def send(self, message: OutboundMessage) -> SendResult:
        raise NotImplementedError
