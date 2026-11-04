"""Foto de perfil do contato no canal — baixada uma vez, servida por link assinado.

**Por que não guardar a URL que o provider devolve.** O Evolution responde com um
endereço do CDN do WhatsApp: ele expira sozinho em algumas horas e, enquanto vale, é uma
URL pública que identifica o cliente para qualquer um que veja o HTML da inbox. Guardar
aquilo daria imagem quebrada em um dia e vazamento no outro. Baixamos o arquivo, ele
passa a viver como toda mídia (`MEDIA_DIR/{tenant}/avatars/`), e o front recebe uma
assinatura curta como a de qualquer anexo (`CLAUDE.md` regra 6).

**Por que `channel_metadata` e não `profile`.** `contacts.profile` é reescrito INTEIRO a
cada turno do bot (`contact_memory.remember`): um avatar gravado ali some sem erro nenhum
na primeira mensagem que o cliente mandar. `channel_metadata` é o lugar do que é do canal,
por convenção do projeto, e a escrita aqui é `||` em SQL — nunca ler-mesclar-gravar.

**Por que preguiçoso.** Não há `celery beat` no projeto e não se cria um para isto: a
ingestão enfileira a busca quando percebe que o avatar falta ou envelheceu, do mesmo jeito
que `salao_agenda.expirar_vencidas` e o encerramento por inatividade acontecem na leitura.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import media_links, storage

TIPO_LINK = "avatar"

# Foto de perfil muda, e ninguém avisa. Uma semana é o meio-termo entre a inbox mostrar
# uma foto antiga e o worker baixar imagem a cada mensagem. Vale igual para o sucesso e
# para a falha: contato sem foto visível (privacidade) é o caso COMUM, e sem registrar a
# tentativa a gente perguntaria ao Evolution em todo turno de toda conversa.
VALIDADE_DIAS = 7

# Por quanto tempo o navegador guarda a foto, e por quanto tempo a URL assinada fica
# IGUAL — os dois andam juntos. A lista da inbox remonta a cada evento de WebSocket e
# re-assina toda foto; com `exp` novo a cada chamada, o cache nunca acertava e cada
# mensagem na conta rebaixava todas as fotos da lista pela API.
CACHE_SEGUNDOS = 3600

STATUS_STORED = "stored"
STATUS_UNAVAILABLE = "unavailable"


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def desatualizado(channel_metadata: dict | None) -> bool:
    """Vale a pena (re)buscar a foto deste contato?"""
    avatar = (channel_metadata or {}).get("avatar") or {}
    quando = avatar.get("fetched_at")
    if not quando:
        return True
    try:
        visto = datetime.fromisoformat(str(quando))
    except ValueError:
        return True
    if visto.tzinfo is None:
        visto = visto.replace(tzinfo=timezone.utc)
    return _agora() - visto > timedelta(days=VALIDADE_DIAS)


def caminho_do_arquivo(tenant_id: UUID | str, contact_id: UUID | str, ext: str) -> str:
    destino = os.path.join(settings.MEDIA_DIR, str(tenant_id), "avatars")
    os.makedirs(destino, exist_ok=True)
    return os.path.join(destino, f"{contact_id}{ext}")


async def registrar(
    db: AsyncSession, tenant_id: UUID, contact_id: UUID, avatar: dict
) -> None:
    """Grava o resultado da busca sob a chave `avatar` do `channel_metadata`.

    `||` em SQL, e não SELECT + merge em Python: o contato é escrito por vários caminhos
    ao mesmo tempo (ingestão, bot, painel) e ler-mesclar-gravar aqui perderia o que outro
    turno acabou de escrever — a corrida das regras 2 e 3 num campo que ninguém suspeita.
    """
    await db.execute(
        text(
            "UPDATE contacts SET channel_metadata = "
            "  COALESCE(channel_metadata, '{}'::jsonb) "
            "  || jsonb_build_object('avatar', CAST(:avatar AS jsonb)) "
            "WHERE id = :cid AND tenant_id = :tid"
        ),
        {
            "avatar": json.dumps({**avatar, "fetched_at": _agora().isoformat()}),
            "cid": str(contact_id),
            "tid": str(tenant_id),
        },
    )


def url_de(
    tenant_id: UUID | str, contact_id: UUID | str, channel_metadata: dict | None
) -> str | None:
    """Caminho + assinatura, ou `None` quando não há foto guardada.

    `None` é resposta legítima e frequente — a maioria dos contatos não expõe foto. O
    front cai nas iniciais, que é o desenho que já existia.
    """
    avatar = (channel_metadata or {}).get("avatar") or {}
    if avatar.get("status") != STATUS_STORED:
        return None
    return (
        f"/contacts/{contact_id}/avatar"
        f"?{media_links.assinar(TIPO_LINK, contact_id, tenant_id, janela=CACHE_SEGUNDOS)}"
    )


async def tenant_de(db: AsyncSession, contact_id: UUID) -> UUID | None:
    """Dono do contato, ou None. Existe para validar a assinatura da URL.

    A assinatura é feita sobre (contato, tenant), então validar exige saber o tenant
    antes de confiar em quem pede — daí a leitura sem escopo aqui. Nada além do id do
    dono sai desta função, e o acesso ao arquivo continua passando por `arquivo_de`, que
    filtra por tenant. Mesmo desenho de `salao_fotos.tenant_de`.
    """
    row = await db.execute(
        text("SELECT tenant_id FROM contacts WHERE id = :id"),
        {"id": str(contact_id)},
    )
    return row.scalar_one_or_none()


async def arquivo_de(
    db: AsyncSession, tenant_id: UUID, contact_id: UUID
) -> tuple[str, str] | None:
    """`(caminho, mimetype)` da foto guardada, ou None."""
    row = await db.execute(
        text(
            "SELECT channel_metadata FROM contacts WHERE id = :cid AND tenant_id = :tid"
        ),
        {"cid": str(contact_id), "tid": str(tenant_id)},
    )
    r = row.fetchone()
    if r is None:
        return None
    avatar = (r.channel_metadata or {}).get("avatar") or {}
    caminho = avatar.get("path")
    if avatar.get("status") != STATUS_STORED or not caminho:
        return None
    if not await storage.exists(caminho):
        return None
    return caminho, str(avatar.get("mimetype") or "image/jpeg")
