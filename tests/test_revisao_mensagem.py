"""Cliente apagou ou editou no WhatsApp: o Otto marca, guarda o original e nunca apaga.

Os payloads abaixo têm o formato que o Evolution 2.2.3 manda de fato (conferido no
código dele): `messages.delete` traz a `key` crua; `messages.edited` traz o
`protocolMessage`, com o texto novo em `editedMessage`.
"""

import json
import uuid

import pytest_asyncio
from sqlalchemy import text

from app.channels.types import NormalizedMessageChange
from app.channels.whatsapp import EvolutionProvider
from app.core.database import AsyncSessionLocal
from app.services import inbox_queries, revisao_mensagem


def _webhook(event: str, data: dict) -> bytes:
    return json.dumps({"event": event, "instance": "i", "data": data}).encode()


class TestParser:
    def test_apagada(self) -> None:
        corpo = _webhook(
            "messages.delete",
            {"remoteJid": "5511@s.whatsapp.net", "fromMe": False, "id": "ABC"},
        )
        assert EvolutionProvider().parse_webhook({}, corpo) == [
            NormalizedMessageChange(
                channel="whatsapp", external_id="ABC", acao="apagada"
            )
        ]

    def test_editada(self) -> None:
        corpo = _webhook(
            "messages.edited",
            {
                "key": {
                    "remoteJid": "5511@s.whatsapp.net",
                    "fromMe": False,
                    "id": "ABC",
                },
                "type": 14,
                "editedMessage": {"conversation": "na verdade é dia 13"},
            },
        )
        [mudanca] = EvolutionProvider().parse_webhook({}, corpo)
        assert mudanca.acao == "editada"
        assert mudanca.content == "na verdade é dia 13"

    def test_edicao_sem_texto_e_ignorada(self) -> None:
        corpo = _webhook("messages.edited", {"key": {"id": "ABC"}, "editedMessage": {}})
        assert EvolutionProvider().parse_webhook({}, corpo) == []

    def test_webhook_assina_os_eventos(self) -> None:
        eventos = EvolutionProvider()._webhook_config("x")["events"]
        assert {"MESSAGES_DELETE", "MESSAGES_EDITED"} <= set(eventos)


@pytest_asyncio.fixture
async def conversa():
    """Dois tenants; a mensagem com id externo EXT pertence ao primeiro."""
    ext = f"EXT-{uuid.uuid4().hex[:8]}"
    criados = []
    async with AsyncSessionLocal() as db:
        for _ in range(2):
            tid, conn, contato, conv = (uuid.uuid4() for _ in range(4))
            await db.execute(
                text("INSERT INTO tenants (id, name) VALUES (:id, 'Tenant Revisão')"),
                {"id": str(tid)},
            )
            await db.execute(
                text(
                    "INSERT INTO channel_connections (id, tenant_id, channel, credentials, "
                    "is_active) VALUES (:id, :t, 'WHATSAPP', '{}', true)"
                ),
                {"id": str(conn), "t": str(tid)},
            )
            await db.execute(
                text(
                    "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
                    "VALUES (:id, :t, 'WHATSAPP', '5511944444', 'Cliente')"
                ),
                {"id": str(contato), "t": str(tid)},
            )
            await db.execute(
                text(
                    "INSERT INTO conversations (id, tenant_id, contact_id, status, "
                    "unread_count) VALUES (:id, :t, :c, 'OPEN', 0)"
                ),
                {"id": str(conv), "t": str(tid), "c": str(contato)},
            )
            criados.append({"tenant_id": tid, "conn": str(conn), "conv": conv})
        primeiro = criados[0]
        await db.execute(
            text(
                "INSERT INTO messages (id, conversation_id, tenant_id, direction, "
                "content_type, content, channel, external_id) VALUES (gen_random_uuid(), "
                ":conv, :t, 'INBOUND', 'TEXT', 'quero o dia 12', 'WHATSAPP', :ext)"
            ),
            {
                "conv": str(primeiro["conv"]),
                "t": str(primeiro["tenant_id"]),
                "ext": ext,
            },
        )
        await db.commit()

    yield {"ext": ext, "dono": criados[0], "vizinho": criados[1]}

    async with AsyncSessionLocal() as db:
        for c in criados:
            for tabela in (
                "messages",
                "conversations",
                "contacts",
                "channel_connections",
            ):
                await db.execute(
                    text(f"DELETE FROM {tabela} WHERE tenant_id = :t"),
                    {"t": str(c["tenant_id"])},
                )
            await db.execute(
                text("DELETE FROM tenants WHERE id = :t"), {"t": str(c["tenant_id"])}
            )
        await db.commit()


async def _mensagem(dono: dict) -> dict:
    async with AsyncSessionLocal() as db:
        [m] = await inbox_queries.get_conversation_messages(
            db, dono["tenant_id"], dono["conv"]
        )
    return m


async def test_apagada_fica_visivel_e_marcada(conversa) -> None:
    async with AsyncSessionLocal() as db:
        alvo = await revisao_mensagem.aplicar_do_canal(
            db, conversa["dono"]["conn"], "WHATSAPP", conversa["ext"], "apagada"
        )
        await db.commit()
    assert alvo is not None
    m = await _mensagem(conversa["dono"])
    assert m["deleted_at"] is not None
    assert m["deleted_by"] is None  # veio do canal, não de uma pessoa
    assert m["content"] == "quero o dia 12"


async def test_reeditar_guarda_a_primeira_versao(conversa) -> None:
    for novo in ("quero o dia 13", "quero o dia 14"):
        async with AsyncSessionLocal() as db:
            await revisao_mensagem.aplicar_do_canal(
                db,
                conversa["dono"]["conn"],
                "WHATSAPP",
                conversa["ext"],
                "editada",
                novo,
            )
            await db.commit()
    m = await _mensagem(conversa["dono"])
    assert m["content"] == "quero o dia 14"
    assert m["original_content"] == "quero o dia 12"
    assert m["edited_at"] is not None


async def test_conexao_de_outro_tenant_nao_alcanca(conversa) -> None:
    """O par (canal, id externo) vem do corpo; quem escolhe o tenant é a conexão."""
    async with AsyncSessionLocal() as db:
        alvo = await revisao_mensagem.aplicar_do_canal(
            db, conversa["vizinho"]["conn"], "WHATSAPP", conversa["ext"], "apagada"
        )
        await db.commit()
    assert alvo is None
    assert (await _mensagem(conversa["dono"]))["deleted_at"] is None
