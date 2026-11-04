"""Testes do EvolutionProvider — parse e envio de texto e mídia (sem rede)."""

import base64
import json
import uuid

import httpx
import pytest

from app.channels.types import (
    NormalizedStatusUpdate,
    OutboundMedia,
    OutboundMessage,
)
from app.core.config import settings
from app.channels.whatsapp import EvolutionProvider
from app.models.enums import Channel, ContentType, MessageStatus


def _webhook(
    message_obj: dict, msg_id: str = "ABC123", key_extra: dict | None = None
) -> bytes:
    key = {
        "remoteJid": "5511999@s.whatsapp.net",
        "fromMe": False,
        "id": msg_id,
    }
    key.update(key_extra or {})
    return json.dumps(
        {
            "event": "messages.upsert",
            "instance": "otto-test",
            "data": {
                "key": key,
                "pushName": "Cliente",
                "messageTimestamp": 1700000000,
                "message": message_obj,
            },
        }
    ).encode()


class TestTelefoneDoContato:
    """O `remoteJid` nem sempre é telefone — e tratá-lo como se fosse quebrou a agenda.

    Uma reserva confirmada ficou com `telefone = '21711730823330@lid'`: o dono do salão
    não conseguia ligar para o cliente, e ninguém percebeu porque o campo estava cheio.
    """

    def setup_method(self):
        self.p = EvolutionProvider()

    def _parse(self, key_extra: dict):
        return self.p.parse_webhook(
            {}, _webhook({"conversation": "oi"}, key_extra=key_extra)
        )[0]

    def test_payload_real_do_incidente(self):
        """`@lid` no remoteJid, telefone verdadeiro no senderPn."""
        m = self._parse(
            {
                "remoteJid": "21711730823330@lid",
                "senderPn": "5511000000001@s.whatsapp.net",
            }
        )
        # O ENDEREÇO não muda: é por ele que o envio funciona hoje.
        assert m.contact_external_id == "21711730823330@lid"
        assert m.contact_phone == "5511000000001"

    def test_jid_de_telefone_sem_senderpn(self):
        m = self._parse({"remoteJid": "5511999888777@s.whatsapp.net"})
        assert m.contact_phone == "5511999888777"

    def test_lid_sem_senderpn_nao_inventa_numero(self):
        """Sem fonte confiável, `None`. Os dígitos do `@lid` não são telefone de ninguém."""
        m = self._parse({"remoteJid": "21711730823330@lid"})
        assert m.contact_phone is None
        assert m.contact_external_id == "21711730823330@lid"

    def test_sufixo_de_dispositivo_e_removido(self):
        m = self._parse({"remoteJid": "5511999888777:12@s.whatsapp.net"})
        assert m.contact_phone == "5511999888777"

    @pytest.mark.parametrize("lixo", ["abc@x", "123", "", "@s.whatsapp.net"])
    def test_valor_improvavel_vira_none(self, lixo):
        m = self._parse({"remoteJid": lixo, "senderPn": lixo})
        assert m.contact_phone is None


class TestParseWebhook:
    def setup_method(self):
        self.p = EvolutionProvider()

    def test_texto_simples(self):
        msgs = self.p.parse_webhook({}, _webhook({"conversation": "olá"}))
        assert len(msgs) == 1
        assert msgs[0].content_type == ContentType.TEXT
        assert msgs[0].content == "olá"
        assert "media" not in msgs[0].channel_metadata

    def test_texto_estendido(self):
        msgs = self.p.parse_webhook(
            {}, _webhook({"extendedTextMessage": {"text": "com link"}})
        )
        assert msgs[0].content_type == ContentType.TEXT
        assert msgs[0].content == "com link"

    def test_audio_marca_midia_sem_content(self):
        msgs = self.p.parse_webhook(
            {}, _webhook({"audioMessage": {"mimetype": "audio/ogg; codecs=opus"}})
        )
        assert msgs[0].content_type == ContentType.AUDIO
        assert msgs[0].content is None
        media = msgs[0].channel_metadata["media"]
        assert media["has_media"] is True
        assert media["status"] == "pending"
        assert media["mimetype"] == "audio/ogg; codecs=opus"

    def test_imagem_usa_caption_como_content(self):
        msgs = self.p.parse_webhook(
            {},
            _webhook(
                {"imageMessage": {"mimetype": "image/jpeg", "caption": "olha isso"}}
            ),
        )
        assert msgs[0].content_type == ContentType.IMAGE
        assert msgs[0].content == "olha isso"
        assert msgs[0].channel_metadata["media"]["has_media"] is True

    def test_documento_usa_filename(self):
        msgs = self.p.parse_webhook(
            {},
            _webhook(
                {
                    "documentMessage": {
                        "mimetype": "application/pdf",
                        "fileName": "nota.pdf",
                    }
                }
            ),
        )
        assert msgs[0].content_type == ContentType.DOCUMENT
        assert msgs[0].content == "nota.pdf"

    def test_raw_payload_preservado(self):
        msgs = self.p.parse_webhook(
            {}, _webhook({"audioMessage": {"mimetype": "audio/ogg"}})
        )
        assert msgs[0].channel_metadata["raw"]["data"]["key"]["id"] == "ABC123"

    def test_grupo_ignorado(self):
        payload = json.loads(_webhook({"conversation": "oi"}).decode())
        payload["data"]["key"]["remoteJid"] = "123@g.us"
        assert self.p.parse_webhook({}, json.dumps(payload).encode()) == []

    def test_from_me_ignorado(self):
        payload = json.loads(_webhook({"conversation": "oi"}).decode())
        payload["data"]["key"]["fromMe"] = True
        assert self.p.parse_webhook({}, json.dumps(payload).encode()) == []

    def test_evento_nao_upsert_ignorado(self):
        assert (
            self.p.parse_webhook({}, json.dumps({"event": "presence.update"}).encode())
            == []
        )

    def test_reacao_nao_vira_mensagem(self):
        """Reagir 👍 a uma bolha do bot é gesto comum — e virava um turno inteiro.

        Sem texto e sem mídia conhecida, a reação caía no genérico de `_parse_media`,
        entrava como DOCUMENT na thread e o bot respondia "recebi seu arquivo".
        """
        reacao = {
            "reactionMessage": {
                "key": {"id": "ABC123", "fromMe": True},
                "text": "👍",
            }
        }
        assert self.p.parse_webhook({}, _webhook(reacao)) == []

    def test_apagar_e_votar_em_enquete_tambem_sao_ignorados(self):
        assert (
            self.p.parse_webhook({}, _webhook({"protocolMessage": {"type": 0}})) == []
        )
        assert self.p.parse_webhook({}, _webhook({"pollUpdateMessage": {}})) == []


def _status_webhook(status_raw: str, msg_id: str = "MSG1") -> bytes:
    """Formato ANINHADO — o que o `CloudAPIProvider` usaria. Ver `_status_evolution`."""
    return json.dumps(
        {
            "event": "messages.update",
            "instance": "otto-test",
            "data": {"key": {"id": msg_id, "fromMe": True}, "status": status_raw},
        }
    ).encode()


def _status_evolution(status_raw: str, msg_id: str = "MSG1") -> bytes:
    """O payload REAL do Evolution, copiado do objeto que ele monta antes do webhook.

    Plano, com `keyId` — não existe `key` aqui. Os testes acima passavam com o parser
    quebrado porque exercitavam só o formato que nós tínhamos imaginado.
    """
    return json.dumps(
        {
            "event": "messages.update",
            "instance": "otto-test",
            "data": {
                "messageId": "cm-interno-do-evolution",
                "keyId": msg_id,
                "remoteJid": "5511000000002@s.whatsapp.net",
                "fromMe": True,
                "participant": "5511000000002@s.whatsapp.net",
                "status": status_raw,
                "instanceId": "inst-1",
            },
        }
    ).encode()


class TestParseStatus:
    def setup_method(self):
        self.p = EvolutionProvider()

    def test_delivery_ack_vira_delivered(self):
        [upd] = self.p.parse_webhook({}, _status_webhook("DELIVERY_ACK"))
        assert isinstance(upd, NormalizedStatusUpdate)
        assert upd.status == MessageStatus.DELIVERED
        assert upd.external_id == "MSG1"

    def test_read_vira_read(self):
        [upd] = self.p.parse_webhook({}, _status_webhook("READ"))
        assert upd.status == MessageStatus.READ

    def test_error_vira_failed(self):
        [upd] = self.p.parse_webhook({}, _status_webhook("ERROR"))
        assert upd.status == MessageStatus.FAILED

    def test_status_desconhecido_ignorado(self):
        assert self.p.parse_webhook({}, _status_webhook("WHATEVER")) == []

    def test_sem_id_ignorado(self):
        assert self.p.parse_webhook({}, _status_webhook("READ", msg_id="")) == []

    # ── o formato que o Evolution manda de verdade ──
    # Enquanto estes não existiram, todo ACK caía fora em silêncio e nenhuma mensagem
    # saía de SENT — nem as que o WhatsApp recusou.

    def test_payload_plano_do_evolution_vira_delivered(self):
        [upd] = self.p.parse_webhook({}, _status_evolution("DELIVERY_ACK"))
        assert upd.status == MessageStatus.DELIVERED
        assert upd.external_id == "MSG1"

    def test_payload_plano_error_vira_failed(self):
        """O caso que mais custa: falha de entrega tem que chegar na Inbox."""
        [upd] = self.p.parse_webhook({}, _status_evolution("ERROR"))
        assert upd.status == MessageStatus.FAILED
        assert upd.external_id == "MSG1"

    def test_payload_plano_read_vira_read(self):
        [upd] = self.p.parse_webhook({}, _status_evolution("READ"))
        assert upd.status == MessageStatus.READ

    def test_payload_plano_sem_keyid_ignorado(self):
        assert self.p.parse_webhook({}, _status_evolution("READ", msg_id="")) == []


class TestWebhookConfig:
    """O webhook registrado no Evolution tem que satisfazer o nosso próprio verify_webhook.

    Estas duas pontas já divergiram uma vez: o provision registrava url + events sem
    `headers`, e o verify_webhook recusava com 401 tudo que chegasse sem `apikey`. O
    resultado era WhatsApp pareado, instância `open`, webhook `enabled` — e nenhuma
    mensagem na inbox, sem erro visível de nenhum dos dois lados.
    """

    def setup_method(self):
        self.p = EvolutionProvider()

    def test_config_manda_a_apikey_que_o_verify_exige(self, monkeypatch):
        monkeypatch.setattr(settings, "EVOLUTION_API_KEY", "segredo-123")
        monkeypatch.setattr(settings, "WEBHOOK_BASE_URL", "http://otto.local:8000")

        cfg = self.p._webhook_config("wh-abc")

        # A ponta que envia e a ponta que valida têm que concordar.
        assert self.p.verify_webhook(cfg["headers"], b"") is True

    def test_config_aponta_para_a_rota_com_o_webhook_id(self, monkeypatch):
        monkeypatch.setattr(settings, "WEBHOOK_BASE_URL", "http://otto.local:8000/")
        cfg = self.p._webhook_config("wh-abc")
        # A barra final da base não pode virar barra dupla na URL. E o webhook_id tem que
        # estar no caminho: é ele que identifica a conexão (e portanto o tenant) na
        # entrada, em vez do campo `instance` do corpo.
        assert cfg["url"] == "http://otto.local:8000/webhooks/whatsapp/wh-abc"
        assert cfg["enabled"] is True
        assert "MESSAGES_UPSERT" in cfg["events"]

    def test_apikey_errada_recusada(self, monkeypatch):
        monkeypatch.setattr(settings, "EVOLUTION_API_KEY", "segredo-123")
        assert self.p.verify_webhook({"apikey": "outra"}, b"") is False
        assert self.p.verify_webhook({}, b"") is False


class TestSend:
    """Envio: o dispatcher texto × mídia, sem rede.

    O teste de não-regressão do texto é tão importante quanto o da mídia — a mudança
    transformou um `send` linear num dispatcher, e uma mensagem de texto indo parar em
    /sendMedia seria um erro sem sintoma óbvio no log.
    """

    # Na classe, e não no módulo: o resto do arquivo é síncrono.
    pytestmark = pytest.mark.asyncio

    def setup_method(self):
        self.p = EvolutionProvider()
        self.capturado: dict = {}

    def _mock_post(self, monkeypatch, *, status_code=200, payload=None):
        capturado = self.capturado

        async def fake_post(_self, url, *args, **kwargs):
            capturado["url"] = url
            capturado["json"] = kwargs.get("json")
            capturado["timeout"] = kwargs.get("timeout")
            return httpx.Response(status_code, json=payload or {"key": {"id": "EVO-1"}})

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    def _msg(self, **kwargs) -> OutboundMessage:
        base = dict(
            channel=Channel.WHATSAPP,
            channel_connection_id=uuid.uuid4(),
            to_external_id="5511999@s.whatsapp.net",
            content_type=ContentType.TEXT,
            content="oi",
            channel_metadata={"instance_key": "otto-test"},
        )
        base.update(kwargs)
        return OutboundMessage(**base)

    async def test_texto_continua_indo_para_sendtext(self, monkeypatch):
        self._mock_post(monkeypatch)
        r = await self.p.send(self._msg())
        assert r.success is True and r.external_id == "EVO-1"
        assert self.capturado["url"].endswith("/message/sendText/otto-test")
        assert self.capturado["json"]["text"] == "oi"

    async def test_nota_de_voz_vai_para_sendwhatsappaudio(self, monkeypatch):
        """PTT usa OUTRO endpoint — é o que produz o balão de voz, não um anexo.

        `sendMedia` com mediatype=audio entrega um arquivo de áudio com nome, que no
        aparelho do cliente é uma coisa visivelmente diferente de uma nota de voz.
        """
        self._mock_post(monkeypatch)
        r = await self.p.send(
            self._msg(
                content_type=ContentType.AUDIO,
                content="",
                media=OutboundMedia(
                    data=b"opus-bytes",
                    filename="nota.ogg",
                    mimetype="audio/ogg",
                    ptt=True,
                ),
            )
        )
        assert r.success is True and r.external_id == "EVO-1"
        assert self.capturado["url"].endswith("/message/sendWhatsAppAudio/otto-test")
        p = self.capturado["json"]
        assert p["audio"] == base64.b64encode(b"opus-bytes").decode()
        # `mimetype`/`fileName` de propósito ausentes: o container de origem varia por
        # navegador e mandá-lo atrapalha a conversão do Evolution.
        assert "mimetype" not in p and "fileName" not in p

    async def test_audio_sem_ptt_continua_como_anexo(self, monkeypatch):
        """Anexar um .mp3 não é gravar uma nota de voz — os dois caminhos coexistem."""
        self._mock_post(monkeypatch)
        await self.p.send(
            self._msg(
                content_type=ContentType.AUDIO,
                content="",
                media=OutboundMedia(
                    data=b"mp3", filename="musica.mp3", mimetype="audio/mpeg"
                ),
            )
        )
        assert self.capturado["url"].endswith("/message/sendMedia/otto-test")
        assert self.capturado["json"]["mediatype"] == "audio"

    async def test_ptt_em_imagem_e_ignorado(self, monkeypatch):
        """A flag só faz sentido em áudio; em imagem não pode desviar o envio."""
        self._mock_post(monkeypatch)
        await self.p.send(
            self._msg(
                content_type=ContentType.IMAGE,
                content="",
                media=OutboundMedia(
                    data=b"png", filename="f.png", mimetype="image/png", ptt=True
                ),
            )
        )
        assert self.capturado["url"].endswith("/message/sendMedia/otto-test")

    async def test_imagem_vai_para_sendmedia_em_base64(self, monkeypatch):
        self._mock_post(monkeypatch)
        r = await self.p.send(
            self._msg(
                content_type=ContentType.IMAGE,
                content="olha a foto",
                media=OutboundMedia(
                    data=b"\x89PNG-bytes", filename="foto.png", mimetype="image/png"
                ),
            )
        )
        assert r.success is True
        assert self.capturado["url"].endswith("/message/sendMedia/otto-test")
        p = self.capturado["json"]
        assert p["mediatype"] == "image"
        assert p["mimetype"] == "image/png"
        assert p["fileName"] == "foto.png"
        assert base64.b64decode(p["media"]) == b"\x89PNG-bytes"
        assert p["caption"] == "olha a foto"

    async def test_sem_legenda_nao_manda_caption(self, monkeypatch):
        """String vazia viraria uma legenda em branco no balão do cliente."""
        self._mock_post(monkeypatch)
        await self.p.send(
            self._msg(
                content_type=ContentType.DOCUMENT,
                content="",
                media=OutboundMedia(
                    data=b"%PDF", filename="orcamento.pdf", mimetype="application/pdf"
                ),
            )
        )
        assert "caption" not in self.capturado["json"]
        assert self.capturado["json"]["mediatype"] == "document"

    async def test_timeout_de_midia_e_maior_que_o_de_texto(self, monkeypatch):
        """16 MB de vídeo não sobem nos 10s que bastam para uma linha de texto."""
        self._mock_post(monkeypatch)
        await self.p.send(self._msg())
        timeout_texto = self.capturado["timeout"]
        await self.p.send(
            self._msg(
                content_type=ContentType.VIDEO,
                media=OutboundMedia(
                    data=b"video", filename="v.mp4", mimetype="video/mp4"
                ),
            )
        )
        assert self.capturado["timeout"] > timeout_texto

    async def test_erro_http_vira_sendresult_falho(self, monkeypatch):
        self._mock_post(monkeypatch, status_code=400, payload={"error": "bad"})
        r = await self.p.send(
            self._msg(
                content_type=ContentType.IMAGE,
                media=OutboundMedia(data=b"x", filename="a.png", mimetype="image/png"),
            )
        )
        assert r.success is False and r.external_id is None


class TestPresencaELeitura:
    """Tique azul e "digitando…".

    Os dois são best-effort por natureza: acontecem no aparelho de OUTRA pessoa, como
    efeito secundário de uma ação do atendente. O que estes testes travam é que falha
    aqui devolve False em vez de propagar — a rota de abrir conversa não pode quebrar
    por causa de um tique.
    """

    pytestmark = pytest.mark.asyncio

    def setup_method(self):
        self.p = EvolutionProvider()
        self.capturado: dict = {}

    def _mock_post(self, monkeypatch, *, status_code=200, raises=None):
        capturado = self.capturado

        async def fake_post(_self, url, *args, **kwargs):
            if raises:
                raise raises
            capturado["url"] = url
            capturado["json"] = kwargs.get("json")
            return httpx.Response(status_code, json={})

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    async def test_mark_read_marca_so_as_do_cliente(self, monkeypatch):
        self._mock_post(monkeypatch)
        ok = await self.p.mark_read("otto-test", "5511999@s.whatsapp.net", ["A", "B"])
        assert ok is True
        assert self.capturado["url"].endswith("/chat/markMessageAsRead/otto-test")
        lidas = self.capturado["json"]["readMessages"]
        assert [m["id"] for m in lidas] == ["A", "B"]
        # fromMe=False em todas: marcar a PRÓPRIA mensagem como lida não quer dizer nada.
        assert all(m["fromMe"] is False for m in lidas)

    async def test_mark_read_limita_o_lote(self, monkeypatch):
        """Conversa antiga com centenas de não-lidas não vira um payload gigante."""
        self._mock_post(monkeypatch)
        await self.p.mark_read(
            "otto-test", "5511999@s.whatsapp.net", [str(i) for i in range(200)]
        )
        assert len(self.capturado["json"]["readMessages"]) == 50

    async def test_mark_read_sem_ids_nao_chama(self, monkeypatch):
        self._mock_post(monkeypatch)
        assert (
            await self.p.mark_read("otto-test", "5511999@s.whatsapp.net", []) is False
        )
        assert self.capturado == {}

    async def test_mark_read_engole_erro_de_rede(self, monkeypatch):
        """O atendente não pode perder a thread porque o tique falhou."""
        self._mock_post(monkeypatch, raises=httpx.ConnectError("down"))
        assert (
            await self.p.mark_read("otto-test", "5511999@s.whatsapp.net", ["A"])
            is False
        )

    async def test_presence_envia_composing(self, monkeypatch):
        self._mock_post(monkeypatch)
        ok = await self.p.send_presence("otto-test", "5511999@s.whatsapp.net")
        assert ok is True
        assert self.capturado["url"].endswith("/chat/sendPresence/otto-test")
        assert self.capturado["json"]["presence"] == "composing"
        # Curto: o indicador expira sozinho, e valor alto deixa "digitando" preso na
        # tela do cliente depois que o atendente já parou.
        assert self.capturado["json"]["delay"] == 3000

    async def test_presence_engole_erro_de_rede(self, monkeypatch):
        self._mock_post(monkeypatch, raises=httpx.ConnectError("down"))
        assert (
            await self.p.send_presence("otto-test", "5511999@s.whatsapp.net") is False
        )

    async def test_sem_instancia_nao_chama(self, monkeypatch):
        """Conversa órfã (reconexão do WhatsApp) não pode virar URL com instância vazia."""
        self._mock_post(monkeypatch)
        assert await self.p.send_presence("", "5511999@s.whatsapp.net") is False
        assert await self.p.mark_read("", "5511999@s.whatsapp.net", ["A"]) is False
        assert self.capturado == {}


class TestDestinoDoEnvio:
    """Para onde o `number` do Evolution aponta quando o endereço é um `@lid`.

    O bug que motivou isto não tinha erro nenhum: o Evolution devolvia 201 com uma key
    de mensagem de verdade, a Inbox marcava SENT, o "digitando…" aparecia no aparelho do
    cliente — e a mensagem não chegava. O `@lid` roteia presença e não roteia mensagem.
    Por isso os testes olham o CAMPO `number` do payload, e não o `success`: sucesso é
    exatamente o que o caso quebrado também devolvia.
    """

    pytestmark = pytest.mark.asyncio

    def setup_method(self):
        self.p = EvolutionProvider()
        self.capturado: dict = {}

    def _mock_post(self, monkeypatch):
        capturado = self.capturado

        async def fake_post(_self, url, *args, **kwargs):
            capturado["json"] = kwargs.get("json")
            return httpx.Response(200, json={"key": {"id": "EVO-1"}})

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    def _msg(self, **kwargs) -> OutboundMessage:
        base = dict(
            channel=Channel.WHATSAPP,
            channel_connection_id=uuid.uuid4(),
            to_external_id="46781035495449@lid",
            to_phone="5511000000002",
            content_type=ContentType.TEXT,
            content="oi",
            channel_metadata={"instance_key": "otto-test"},
        )
        base.update(kwargs)
        return OutboundMessage(**base)

    async def test_lid_com_telefone_manda_no_telefone(self, monkeypatch):
        self._mock_post(monkeypatch)
        await self.p.send(self._msg())
        assert self.capturado["json"]["number"] == "5511000000002"

    async def test_lid_na_midia_tambem(self, monkeypatch):
        """A galeria do salão sai por /sendMedia — mesmo endereço, mesmo sumiço."""
        self._mock_post(monkeypatch)
        await self.p.send(
            self._msg(
                content_type=ContentType.IMAGE,
                content="",
                media=OutboundMedia(
                    data=b"jpeg", filename="foto.jpg", mimetype="image/jpeg"
                ),
            )
        )
        assert self.capturado["json"]["number"] == "5511000000002"

    async def test_lid_na_nota_de_voz_tambem(self, monkeypatch):
        self._mock_post(monkeypatch)
        await self.p.send(
            self._msg(
                content_type=ContentType.AUDIO,
                content="",
                media=OutboundMedia(
                    data=b"opus", filename="nota.ogg", mimetype="audio/ogg", ptt=True
                ),
            )
        )
        assert self.capturado["json"]["number"] == "5511000000002"

    async def test_endereco_que_roteia_continua_mandando(self, monkeypatch):
        """Sem `@lid`, nada muda — o telefone conhecido NÃO sequestra o endereço.

        As conversas antigas e as de teste têm `@s.whatsapp.net`, que entrega. Preferir
        o telefone ali seria trocar um endereço bom por um palpite.
        """
        self._mock_post(monkeypatch)
        await self.p.send(
            self._msg(to_external_id="5511999@s.whatsapp.net", to_phone="5511888")
        )
        assert self.capturado["json"]["number"] == "5511999@s.whatsapp.net"

    async def test_lid_sem_telefone_falha_em_vez_de_fingir(self, monkeypatch):
        """Sem telefone, o lid não entrega — e fingir `SENT` é pior que falhar.

        Nada chega ao Evolution: a falha é o que aciona o desvio para a fila da equipe,
        com o motivo gravado na mensagem. Antes disto, o atendente via "enviado" numa
        mensagem que o WhatsApp devolveu como `ERROR` minutos depois.
        """
        self._mock_post(monkeypatch)
        r = await self.p.send(self._msg(to_phone=None))
        assert r.success is False
        assert "@lid" in (r.error or "")
        assert self.capturado == {}
