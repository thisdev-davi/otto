"""O roteiro determinístico atendendo de verdade, pelo mesmo caminho do agente de IA.

Estes testes entram por `handle_inbound` de propósito. O valor do desenho é que o fluxo
NÃO tem caminho próprio de envio: se ele passar por aqui, ele herdou anti-flood, bolhas
com cadência, handoff atômico, ações do salão e persistência sem uma linha nova. Testar
`engine.decidir` isolado provaria menos.
"""

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models.enums import ContentType
from app.models.salao import ReservaStatus
from app.services import bot_orchestrator, flow_store, salao_agenda
from app.services.bot_orchestrator import SkipReason, handle_inbound
from app.services.bot_settings import MODO_FLUXO, BotConfig

pytestmark = pytest.mark.asyncio

FLUXO_PADRAO = json.loads(
    (Path(__file__).resolve().parent.parent / "conteudo" / "fluxo.json").read_text(
        encoding="utf-8"
    )
)
DATA_LIVRE = date.today() + timedelta(days=45)
PRECO = 420_000


class _FakeRedis:
    async def publish(self, *args, **kwargs) -> None:
        PUBLICADOS.append(json.loads(args[-1]))


# O que foi publicado no canal do tenant no turno. Vive fora da instância porque
# `_cliente_diz` cria um `_FakeRedis` novo a cada mensagem, como o worker faz.
PUBLICADOS: list[dict] = []


@pytest_asyncio.fixture
async def cenario(monkeypatch) -> dict:
    """Tenant em modo fluxo, com o roteiro do repositório publicado e uma data à venda."""
    PUBLICADOS.clear()
    tenant_id, contact_id, conn_id, conv_id, user_id = (uuid.uuid4() for _ in range(5))

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'Test Fluxo')"),
            {"id": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, hashed_password, is_active) "
                "VALUES (:id, :t, 'fluxo@bot.com', 'x', true)"
            ),
            {"id": str(user_id), "t": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO channel_connections "
                "(id, tenant_id, channel, credentials, is_active) VALUES "
                "(:id, :t, 'WHATSAPP', '{\"instance\": \"fluxo-test\"}'::jsonb, true)"
            ),
            {"id": str(conn_id), "t": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, channel, external_id, name, phone) "
                "VALUES (:id, :t, 'WHATSAPP', '5511955555', 'Lucas', '5511955555')"
            ),
            {"id": str(contact_id), "t": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO conversations "
                "(id, tenant_id, contact_id, channel_connection_id, status, unread_count) "
                "VALUES (:id, :t, :c, :conn, 'OPEN', 0)"
            ),
            {
                "id": str(conv_id),
                "t": str(tenant_id),
                "c": str(contact_id),
                "conn": str(conn_id),
            },
        )
        await flow_store.salvar(db, tenant_id, FLUXO_PADRAO, "Teste")
        await salao_agenda.definir(db, tenant_id, DATA_LIVRE, PRECO)
        await db.commit()

    yield {
        "tenant_id": tenant_id,
        "conv_id": conv_id,
        "contact_id": contact_id,
        "user_id": user_id,
    }

    async with AsyncSessionLocal() as db:
        for tabela in (
            "salao_reservas",
            "salao_datas",
            "bot_sessions",
            "messages",
            "conversation_events",
            "conversations",
            "contacts",
            "channel_connections",
            "bot_flows",
            "bot_settings",
            "users",
        ):
            await db.execute(
                text(f"DELETE FROM {tabela} WHERE tenant_id = :t"),
                {"t": str(tenant_id)},
            )
        await db.execute(
            text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
        )
        await db.commit()


def _modo_fluxo(monkeypatch, **extra) -> list[str]:
    """Liga o modo fluxo e captura o que seria enviado ao WhatsApp."""
    enviadas: list[str] = []

    async def fake_load(db, tenant_id):
        return BotConfig(ai_enabled=True, modo=MODO_FLUXO, provider="stub", **extra)

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    monkeypatch.setattr(bot_orchestrator, "load_bot_config", fake_load)
    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)
    return enviadas


async def _cliente_diz(cenario: dict, texto: str) -> None:
    """Grava a mensagem do cliente e roda o turno, como a ingestão faria."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO messages "
                "(id, conversation_id, tenant_id, direction, content_type, content, "
                " channel, created_at) "
                "VALUES (gen_random_uuid(), :c, :t, 'INBOUND', 'TEXT', :m, "
                "'WHATSAPP', now())"
            ),
            {"c": str(cenario["conv_id"]), "t": str(cenario["tenant_id"]), "m": texto},
        )
        await db.commit()
    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            cenario["tenant_id"],
            cenario["conv_id"],
            ContentType.TEXT.name,
            message_ts=datetime.now(timezone.utc),
        )


async def _cliente_manda(cenario: dict, tipo: ContentType) -> None:
    """Grava uma mensagem que NÃO é texto e roda o turno, como a ingestão faria."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO messages "
                "(id, conversation_id, tenant_id, direction, content_type, content, "
                " channel, created_at) "
                "VALUES (gen_random_uuid(), :c, :t, 'INBOUND', :ct, NULL, "
                "'WHATSAPP', clock_timestamp())"
            ),
            {
                "c": str(cenario["conv_id"]),
                "t": str(cenario["tenant_id"]),
                "ct": tipo.name,
            },
        )
        await db.commit()
    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            cenario["tenant_id"],
            cenario["conv_id"],
            tipo.name,
            message_ts=datetime.now(timezone.utc),
        )


async def _estado(conv_id: uuid.UUID) -> dict:
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT variables, handoff, skip_reason FROM bot_sessions "
                "WHERE conversation_id = :c"
            ),
            {"c": str(conv_id)},
        )
        r = row.fetchone()
        if r is None:
            return {}
        return {
            "variables": dict(r.variables or {}),
            "handoff": r.handoff,
            "skip_reason": r.skip_reason,
        }


async def _notas(tenant_id: uuid.UUID) -> list[str]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            text(
                "SELECT content FROM messages WHERE tenant_id = :t "
                "AND channel_metadata->>'internal' = 'true' ORDER BY created_at"
            ),
            {"t": str(tenant_id)},
        )
        return [r.content for r in rows.fetchall()]


# ─────────────────────────── o caminho feliz ───────────────────────────


async def test_primeira_mensagem_sauda_e_abre_o_menu(cenario, monkeypatch) -> None:
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")

    tudo = "\n".join(enviadas)
    assert "Bia" in tudo
    assert "1. Ver datas e valores" in tudo
    assert "6. Falar com um atendente" in tudo
    estado = await _estado(cenario["conv_id"])
    assert estado["variables"]["fluxo"]["no"] == "menu_principal"
    assert estado["skip_reason"] == SkipReason.OK


async def test_data_livre_informa_o_valor_que_veio_da_agenda(
    cenario, monkeypatch
) -> None:
    """O valor NÃO está escrito no roteiro: ele é interpolado pelo nó `agenda`."""
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    enviadas.clear()
    await _cliente_diz(cenario, "1")
    assert any("Para qual data" in m for m in enviadas)

    enviadas.clear()
    await _cliente_diz(cenario, DATA_LIVRE.strftime("%d/%m/%Y"))
    tudo = "\n".join(enviadas)
    assert salao_agenda.formatar_preco(PRECO) in tudo
    assert DATA_LIVRE.strftime("%d/%m/%Y") in tudo
    assert (await _estado(cenario["conv_id"]))["variables"]["fluxo"][
        "no"
    ] == "menu_reserva"


async def test_data_nao_cadastrada_responde_e_oferece_as_livres(
    cenario, monkeypatch
) -> None:
    """O caso que mais custa. Data sem cadastro é a maioria das perguntas de um salão que
    só vende fim de semana — mandar toda quinta-feira para a fila entope a Inbox de
    conversa que o roteiro sabe responder. O que ele NÃO pode é falar em valor: não veio
    valor nenhum da agenda, e nenhum nó deste caminho tem de onde tirar um."""
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    await _cliente_diz(cenario, "1")
    enviadas.clear()

    sem_cadastro = date.today() + timedelta(days=200)
    await _cliente_diz(cenario, sem_cadastro.strftime("%d/%m/%Y"))

    tudo = "\n".join(enviadas)
    assert "agenda ainda não está aberta" in tudo
    # O "não" só vale se vier com alternativa — a data à venda do cenário aparece, com o
    # valor que veio da agenda.
    assert DATA_LIVRE.strftime("%d/%m") in tudo
    assert salao_agenda.formatar_preco(PRECO) in tudo
    # E a prova de que nada foi inventado para a data PERGUNTADA: ela nem é citada, então
    # não há como um valor ter sido atribuído a ela. Todo preço no texto pertence a uma
    # data que a agenda devolveu.
    assert sem_cadastro.strftime("%d/%m") not in tudo
    assert (await _estado(cenario["conv_id"]))["handoff"] is False


async def test_data_nao_cadastrada_com_agenda_vazia_encaminha(
    cenario, monkeypatch
) -> None:
    """Sem NENHUMA data à venda, a resposta acima vira mentira com cara de confiança.

    É o estado de todo cliente novo antes de preencher a agenda: responder "essa data não
    está aberta, veja as próximas: nenhuma" seria o bot despachando sozinho todo mundo que
    pergunta por data. Aqui a saída certa é gente, com nota dizendo o que falta cadastrar.
    """
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM salao_datas WHERE tenant_id = :t"),
            {"t": str(cenario["tenant_id"])},
        )
        await db.commit()

    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    await _cliente_diz(cenario, "1")
    enviadas.clear()
    await _cliente_diz(cenario, DATA_LIVRE.strftime("%d/%m/%Y"))

    tudo = "\n".join(enviadas)
    assert "R$" not in tudo
    assert "confirmar essa data com um atendente" in tudo
    assert (await _estado(cenario["conv_id"]))["handoff"] is True
    assert any("NENHUMA data futura" in n for n in await _notas(cenario["tenant_id"]))


async def test_data_cadastrada_sem_valor_encaminha(cenario, monkeypatch) -> None:
    """`sem_preco` continua sendo caso de gente, e a distinção é o ponto.

    Dia aberto de propósito com o valor em branco é ambíguo: dizer "não está aberta"
    contradiz o próprio cadastro, e dizer o valor é impossível. Só `sem_cadastro` ganhou
    resposta automática."""
    outra = DATA_LIVRE + timedelta(days=3)
    async with AsyncSessionLocal() as db:
        await salao_agenda.definir(db, cenario["tenant_id"], outra, None)
        await db.commit()

    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    await _cliente_diz(cenario, "1")
    enviadas.clear()
    await _cliente_diz(cenario, outra.strftime("%d/%m/%Y"))

    tudo = "\n".join(enviadas)
    assert "R$" not in tudo
    assert "preciso confirmar com um atendente" in tudo
    assert (await _estado(cenario["conv_id"]))["handoff"] is True


async def test_data_ocupada_oferece_as_proximas_livres(cenario, monkeypatch) -> None:
    outra = DATA_LIVRE + timedelta(days=7)
    async with AsyncSessionLocal() as db:
        await salao_agenda.definir(db, cenario["tenant_id"], outra, 300_000)
        await salao_agenda.criar_reserva(
            db,
            cenario["tenant_id"],
            DATA_LIVRE,
            status=ReservaStatus.CONFIRMADA,
            criada_por="humano",
        )
        await db.commit()

    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    await _cliente_diz(cenario, "1")
    enviadas.clear()
    await _cliente_diz(cenario, DATA_LIVRE.strftime("%d/%m/%Y"))

    tudo = "\n".join(enviadas)
    assert "já está reservado" in tudo
    assert outra.strftime("%d/%m") in tudo


async def test_pre_reserva_segura_a_data_antes_de_perguntar_o_resto(
    cenario, monkeypatch
) -> None:
    """A ordem é a decisão de produto: segurar primeiro, perguntar depois.

    Três perguntas entre o "quero" e o INSERT são minutos em que outra conversa leva a
    data de quem já se comprometeu.
    """
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    await _cliente_diz(cenario, "1")
    await _cliente_diz(cenario, DATA_LIVRE.strftime("%d/%m/%Y"))
    enviadas.clear()
    await _cliente_diz(cenario, "1")  # quero segurar

    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT status, criada_por, nome_cliente FROM salao_reservas WHERE tenant_id = :t"
            ),
            {"t": str(cenario["tenant_id"])},
        )
        reserva = row.fetchone()
    assert reserva is not None, "a data tinha de estar segurada JÁ neste turno"
    assert reserva.status == ReservaStatus.PRE_RESERVADA
    assert reserva.criada_por == "bot"
    # Nunca nasce anônima: o nome do perfil do WhatsApp entra na hora da escrita.
    assert reserva.nome_cliente == "Lucas"

    tudo = "\n".join(enviadas)
    assert "48 horas" in tudo
    assert "Ainda não é confirmação" in tudo
    assert "Como é o seu nome?" in tudo
    # A equipe é avisada no mesmo turno — alguém precisa confirmar antes do prazo.
    assert (await _estado(cenario["conv_id"]))["handoff"] is True
    assert any(
        "Pré-reserva criada pelo agente" in n
        for n in await _notas(cenario["tenant_id"])
    )


async def test_roteiro_ate_o_fim_deixa_nota_com_o_que_coletou(
    cenario, monkeypatch
) -> None:
    _modo_fluxo(monkeypatch)
    for msg in (
        "oi",
        "1",
        DATA_LIVRE.strftime("%d/%m/%Y"),
        "1",
        "Lucas Mateus",
        "1",
        "80",
    ):
        await _cliente_diz(cenario, msg)

    estado = await _estado(cenario["conv_id"])
    assert estado["variables"]["fluxo"]["no"] == "fim"
    intake = estado["variables"]["intake"]
    assert intake["nome"] == "Lucas Mateus"
    assert intake["tipo_festa"] == "Aniversário"
    assert intake["convidados"] == "80"
    # Chaves de controle nunca viram dado do cliente.
    assert not [k for k in intake if k.startswith("_")]

    notas = await _notas(cenario["tenant_id"])
    assert any("Roteiro concluído" in n and "Lucas Mateus" in n for n in notas)


async def test_duvida_sobre_o_espaco_e_respondida_pelo_roteiro(
    cenario, monkeypatch
) -> None:
    """O nível 1 responde o que o RAG responderia no nível 3, sem chamar modelo nenhum.

    Sem este ramo, "cabe quantas pessoas?" gastava as duas tentativas do `nao_entendi` e
    caía na fila — o roteiro sabia menos sobre o salão do que o folheto dele. Capacidade,
    estrutura e regras são fato estável, então cabem no texto; valor e disponibilidade
    continuam vindo só da agenda.
    """
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    enviadas.clear()

    await _cliente_diz(cenario, "cabe quantas pessoas?")
    await _cliente_diz(cenario, "1")

    tudo = "\n".join(enviadas)
    assert "220 convidados sentados" in tudo
    assert "R$" not in tudo
    estado = await _estado(cenario["conv_id"])
    assert estado["handoff"] is False
    assert estado["variables"]["fluxo"]["no"] == "menu_apos_duvida"


async def test_festa_maior_que_o_salao_ganha_um_aviso_antes_do_fim(
    cenario, monkeypatch
) -> None:
    """Para isto existe o nó `condicao`: 300 convidados cabem, mas não sentados.

    A equipe descobrir o tamanho da festa só na visita é a reunião que não precisava ter
    acontecido — e o aviso sai do mesmo número que já é perguntado para a triagem.
    """
    enviadas = _modo_fluxo(monkeypatch)
    for msg in ("oi", "1", DATA_LIVRE.strftime("%d/%m/%Y"), "1", "Ana", "2"):
        await _cliente_diz(cenario, msg)

    enviadas.clear()
    await _cliente_diz(cenario, "300")

    assert "coquetel" in "\n".join(enviadas)
    estado = await _estado(cenario["conv_id"])
    assert estado["variables"]["fluxo"]["no"] == "fim"
    assert estado["variables"]["intake"]["convidados"] == "300"


# ─────────────────────────── as saídas de emergência ───────────────────────────


async def test_pedir_atendente_encaminha_na_hora(cenario, monkeypatch) -> None:
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    enviadas.clear()
    await _cliente_diz(cenario, "quero falar com um atendente")

    assert (await _estado(cenario["conv_id"]))["handoff"] is True
    assert any("Triagem pelo roteiro" in n for n in await _notas(cenario["tenant_id"]))


async def test_duas_respostas_sem_sentido_encaminham(cenario, monkeypatch) -> None:
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")

    enviadas.clear()
    await _cliente_diz(cenario, "asdfgh")
    assert any("não entendi" in m.lower() for m in enviadas)
    assert (await _estado(cenario["conv_id"]))["handoff"] is False

    enviadas.clear()
    await _cliente_diz(cenario, "qwerty")
    assert (await _estado(cenario["conv_id"]))["handoff"] is True


# ─────────────────────────── mídia no roteiro ───────────────────────────
#
# Mídia era saída antecipada do orquestrador e nunca chegava aqui: frase fixa uma vez
# por conversa e, da segunda em diante, silêncio — sem nota e sem encaminhar.


async def test_foto_avisa_e_repete_o_menu_em_vez_de_pedir_texto_livre(
    cenario, monkeypatch
) -> None:
    """A frase antiga convidava a escrever livremente — e num menu isso é resposta inválida.

    Repetir o nó é o que fecha o beco: o cliente vê de novo as opções numeradas.
    """
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    enviadas.clear()

    await _cliente_manda(cenario, ContentType.IMAGE)

    assert any("não consigo ver imagens" in m for m in enviadas)
    assert any("1. Ver datas e valores" in m for m in enviadas)
    assert (await _estado(cenario["conv_id"]))["handoff"] is False


async def test_segunda_foto_encaminha_com_nota(cenario, monkeypatch) -> None:
    """Quem insiste na foto é quem não consegue escrever o que quer — chama gente."""
    _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")

    await _cliente_manda(cenario, ContentType.IMAGE)
    assert (await _estado(cenario["conv_id"]))["handoff"] is False

    await _cliente_manda(cenario, ContentType.IMAGE)

    assert (await _estado(cenario["conv_id"]))["handoff"] is True
    notas = await _notas(cenario["tenant_id"])
    assert any("uma foto" in n for n in notas)


async def test_foto_e_texto_somam_no_mesmo_contador(cenario, monkeypatch) -> None:
    """Contador único de propósito: foto e depois besteira também termina na equipe."""
    _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")

    await _cliente_manda(cenario, ContentType.IMAGE)
    await _cliente_diz(cenario, "asdfgh")

    assert (await _estado(cenario["conv_id"]))["handoff"] is True


async def test_conversa_que_comeca_por_foto_ainda_sauda(cenario, monkeypatch) -> None:
    """Primeira mensagem é uma foto: avisa que não vê, e cumprimenta na sequência."""
    enviadas = _modo_fluxo(monkeypatch)

    await _cliente_manda(cenario, ContentType.IMAGE)

    assert any("não consigo ver imagens" in m for m in enviadas)
    assert any("1. Ver datas e valores" in m for m in enviadas)
    assert (await _estado(cenario["conv_id"]))["handoff"] is False


async def test_figurinha_nao_diz_que_recebeu_arquivo(cenario, monkeypatch) -> None:
    """Cada tipo tem a sua frase. "Recebi seu arquivo" para uma figurinha soa errado."""
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    enviadas.clear()

    await _cliente_manda(cenario, ContentType.STICKER)

    assert any("figurinha" in m for m in enviadas)
    assert not any("arquivo" in m for m in enviadas)


async def test_menu_aceita_o_texto_da_opcao_e_nao_so_o_numero(
    cenario, monkeypatch
) -> None:
    """Quem responde "quero ver as fotos" não errou o roteiro."""
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    enviadas.clear()
    await _cliente_diz(cenario, "queria ver as fotos do espaço")
    assert any("Olha só" in m for m in enviadas)


async def test_fotos_sem_galeria_cadastrada_encaminha(cenario, monkeypatch) -> None:
    """Prometer foto que não vem é pior que encaminhar."""
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    enviadas.clear()
    await _cliente_diz(cenario, "2")

    assert (await _estado(cenario["conv_id"]))["handoff"] is True
    assert "_acao_fotos" not in (await _estado(cenario["conv_id"]))["variables"].get(
        "intake", {}
    )


async def test_modo_fluxo_sem_roteiro_publicado_diz_por_que_calou(
    cenario, monkeypatch
) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM bot_flows WHERE tenant_id = :t"),
            {"t": str(cenario["tenant_id"])},
        )
        await db.commit()

    _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    assert (await _estado(cenario["conv_id"]))["skip_reason"] == SkipReason.FLOW_MISSING


async def test_roteiro_quebrado_encaminha_e_registra_o_defeito(
    cenario, monkeypatch
) -> None:
    """Nó apagado enquanto a conversa corria. Degradação segura, nunca exceção."""
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")

    grafo = json.loads(json.dumps(FLUXO_PADRAO))
    grafo["nos"]["menu_principal"]["opcoes"][0]["proximo"] = "evaporou"
    async with AsyncSessionLocal() as db:
        await flow_store.salvar(db, cenario["tenant_id"], grafo, "quebrado")
        await db.commit()

    enviadas.clear()
    await _cliente_diz(cenario, "1")

    assert (await _estado(cenario["conv_id"]))["handoff"] is True
    assert enviadas, "o cliente precisa receber a mensagem de transferência"
    assert any("O roteiro travou" in n for n in await _notas(cenario["tenant_id"]))


async def test_humano_no_comando_cala_o_roteiro(cenario, monkeypatch) -> None:
    """Guarda herdada do orquestrador: o roteiro não fala por cima de um atendente."""
    _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE conversations SET assigned_user_id = :u WHERE id = :c"),
            {"u": str(cenario["user_id"]), "c": str(cenario["conv_id"])},
        )
        await db.commit()

    await _cliente_diz(cenario, "1")
    assert (await _estado(cenario["conv_id"]))["skip_reason"] == SkipReason.CLAIMED


async def test_fora_do_expediente_muda_a_saudacao(cenario, monkeypatch) -> None:
    """Sem isto o roteiro promete às 3h da manhã que um atendente já vai falar."""
    fechado = {"fuso": "America/Sao_Paulo", "dias": {str(d): [] for d in range(1, 8)}}
    enviadas = _modo_fluxo(monkeypatch, horario=fechado)
    await _cliente_diz(cenario, "oi")
    assert any("A equipe atende de segunda a sexta" in m for m in enviadas)


async def test_bolha_que_nao_sai_para_o_resto_e_chama_a_equipe(
    cenario, monkeypatch
) -> None:
    """O incidente real: a sessão do WhatsApp cai depois da primeira bolha.

    O cliente ficava com "Oi! 👋 Aqui é a Bia" e mais nada, o turno era gravado como `ok`,
    e ninguém era chamado — enquanto o estado do fluxo tinha avançado assim mesmo, então o
    bot passava a esperar a resposta de um menu que o cliente nunca viu.
    """
    from app.channels.types import SendResult

    enviadas: list[str] = []

    class _CaiDepoisDaPrimeira:
        async def send(self, outbound):
            if enviadas:
                return SendResult(
                    success=False, error='{"message":"Connection Closed"}'
                )
            enviadas.append(outbound.content)
            return SendResult(success=True, external_id="OK1")

    async def fake_load(db, tenant_id):
        return BotConfig(ai_enabled=True, modo=MODO_FLUXO, provider="stub")

    monkeypatch.setattr(bot_orchestrator, "load_bot_config", fake_load)
    monkeypatch.setattr(
        bot_orchestrator, "get_provider", lambda canal: _CaiDepoisDaPrimeira()
    )

    await _cliente_diz(cenario, "oi")

    estado = await _estado(cenario["conv_id"])
    assert estado["skip_reason"] == SkipReason.SEND_FAILED
    # A conversa vai para a fila: alguém precisa retomar do ponto em que o canal parou.
    assert estado["handoff"] is True
    # E o roteiro para de insistir contra o canal que acabou de recusar.
    assert enviadas == ["Oi! 👋 Aqui é a Bia, do Salão Exemplo."]

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            text(
                "SELECT status FROM messages WHERE tenant_id = :t "
                "AND direction = 'OUTBOUND' AND channel_metadata->>'internal' IS NULL"
            ),
            {"t": str(cenario["tenant_id"])},
        )
        status = sorted(r.status for r in rows.fetchall())
    # Três bolhas no turno; a terceira nem chega a ser tentada.
    assert status == ["FAILED", "SENT"]

    notas = await _notas(cenario["tenant_id"])
    assert any("NÃO recebeu o resto" in n for n in notas)

    # E a equipe é AVISADA. Esta é a escalação que mais precisa disso: o cliente recebeu
    # meia resposta e o canal está fora — ninguém vai mandar uma mensagem nova para fazer
    # a tela apitar, porque o cliente está esperando o resto da que já chegou.
    avisos = [e for e in PUBLICADOS if e.get("type") == "conversation.handoff"]
    assert len(avisos) == 1
    assert avisos[0]["resumo"] == bot_orchestrator.MOTIVO_FALHA_DE_ENVIO


# ─────────────────────────── a corrida ───────────────────────────


async def test_dois_turnos_concorrentes_avancam_o_no_uma_vez_so(
    cenario, monkeypatch
) -> None:
    """A invariante do fluxo, na mesma classe das regras 2 e 3 do CLAUDE.md.

    O projeto não tem debounce: cada mensagem dispara seu próprio turno, e ainda existe a
    re-tentativa agendada. Ler `variables.fluxo.no`, decidir em Python e salvar deixaria
    os dois turnos avançarem — o cliente receberia o menu duas vezes, ou pularia uma
    pergunta. Quem arbitra é o UPDATE condicional em `flow_store.avancar`.
    """
    enviadas = _modo_fluxo(monkeypatch)
    await _cliente_diz(cenario, "oi")  # para em menu_principal
    enviadas.clear()

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO messages "
                "(id, conversation_id, tenant_id, direction, content_type, content, "
                " channel, created_at) VALUES "
                "(gen_random_uuid(), :c, :t, 'INBOUND', 'TEXT', '1', 'WHATSAPP', now())"
            ),
            {"c": str(cenario["conv_id"]), "t": str(cenario["tenant_id"])},
        )
        await db.commit()

    # Barreira: os dois turnos só passam da leitura da sessão depois de AMBOS terem lido o
    # mesmo `turno`. É o que torna a corrida determinística, em vez de sorte de scheduler.
    liberado = asyncio.Event()
    chegaram = 0
    carregar_real = flow_store.carregar

    async def carregar_sincronizado(db, tenant_id):
        nonlocal chegaram
        publicado = await carregar_real(db, tenant_id)
        chegaram += 1
        if chegaram == 2:
            liberado.set()
        await asyncio.wait_for(liberado.wait(), timeout=5)
        return publicado

    monkeypatch.setattr(
        bot_orchestrator.flow_engine.flow_store, "carregar", carregar_sincronizado
    )

    async def turno():
        async with AsyncSessionLocal() as db:
            await handle_inbound(
                db,
                _FakeRedis(),
                cenario["tenant_id"],
                cenario["conv_id"],
                ContentType.TEXT.name,
                message_ts=datetime.now(timezone.utc),
            )

    await asyncio.gather(turno(), turno())

    estado = await _estado(cenario["conv_id"])
    assert estado["variables"]["fluxo"]["no"] == "perguntar_data"
    assert estado["variables"]["fluxo"]["turno"] == 2, "o nó avançou mais de uma vez"
    perguntas = [m for m in enviadas if "Para qual data" in m]
    assert len(perguntas) == 1, f"a pergunta saiu {len(perguntas)}x: {enviadas}"
