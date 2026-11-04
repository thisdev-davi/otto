"""O simulador do roteiro e quem manda nele.

A propriedade que estes testes existem para travar é uma só: **o simulador não escreve**.
Se ela cair, alguém testando o roteiro no painel segura a data de um cliente de verdade.
A agenda continua sendo lida, e é de propósito — ver o preço certo é o motivo do
simulador existir.

A trava da EDIÇÃO (painel muda texto, nunca estrutura) é pura e mora em
`tests/test_flow_schema.py`, junto do validador.
"""

import copy
import json
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.flow import engine as flow_engine
from app.services import flow_store, salao_agenda
from app.services import salao_fluxo  # noqa: F401  registra os nós `agenda` e `acao`
from app.services.bot_settings import MODO_FLUXO, BotConfig

pytestmark = pytest.mark.asyncio

GRAFO = json.loads(
    (Path(__file__).resolve().parent.parent / "conteudo" / "fluxo.json").read_text(
        encoding="utf-8"
    )
)
DATA_LIVRE = date.today() + timedelta(days=55)
PRECO = 380_000


# ─────────────────────────── o simulador ───────────────────────────


@pytest_asyncio.fixture
async def tenant() -> uuid.UUID:
    tid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:i, 'Test Fluxo Edicao')"),
            {"i": str(tid)},
        )
        await flow_store.salvar(db, tid, GRAFO, "Teste")
        await salao_agenda.definir(db, tid, DATA_LIVRE, PRECO)
        await db.commit()

    yield tid

    async with AsyncSessionLocal() as db:
        for tabela in ("salao_reservas", "salao_datas", "bot_flows", "bot_settings"):
            await db.execute(
                text(f"DELETE FROM {tabela} WHERE tenant_id = :t"), {"t": str(tid)}
            )
        await db.execute(text("DELETE FROM tenants WHERE id = :i"), {"i": str(tid)})
        await db.commit()


def _bot() -> BotConfig:
    return BotConfig(ai_enabled=True, modo=MODO_FLUXO, provider="stub")


async def test_simulacao_percorre_o_roteiro_com_o_preco_real(tenant) -> None:
    """A agenda é consultada de verdade: ver o valor certo é o motivo do simulador."""
    async with AsyncSessionLocal() as db:
        passos = await flow_engine.simular(
            db, tenant, _bot(), ["oi", "1", DATA_LIVRE.strftime("%d/%m/%Y")]
        )

    assert [p["no"] for p in passos] == [
        "menu_principal",
        "perguntar_data",
        "menu_reserva",
    ]
    assert salao_agenda.formatar_preco(PRECO) in "\n".join(passos[2]["bolhas"])


async def test_simular_pre_reserva_nao_escreve_no_banco(tenant) -> None:
    """A trava que dá sentido ao resto: testar o roteiro não segura data de ninguém."""
    async with AsyncSessionLocal() as db:
        passos = await flow_engine.simular(
            db, tenant, _bot(), ["oi", "1", DATA_LIVRE.strftime("%d/%m/%Y"), "1"]
        )

    assert "48 horas" in "\n".join(passos[-1]["bolhas"])
    async with AsyncSessionLocal() as db:
        reservas = (
            await db.execute(
                text("SELECT count(*) FROM salao_reservas WHERE tenant_id = :t"),
                {"t": str(tenant)},
            )
        ).scalar_one()
    assert reservas == 0, "a simulação criou uma reserva de verdade"


async def test_simulacao_nao_deixa_sessao_nem_mensagem(tenant) -> None:
    async with AsyncSessionLocal() as db:
        await flow_engine.simular(db, tenant, _bot(), ["oi", "1"])
    async with AsyncSessionLocal() as db:
        for tabela in ("bot_sessions", "messages", "conversations"):
            n = (
                await db.execute(
                    text(f"SELECT count(*) FROM {tabela} WHERE tenant_id = :t"),
                    {"t": str(tenant)},
                )
            ).scalar_one()
            assert n == 0, f"a simulação escreveu em {tabela}"


async def test_simulacao_mostra_o_encaminhamento_e_a_nota(tenant) -> None:
    """O painel precisa ver o que a EQUIPE receberia, não só o que o cliente recebe."""
    async with AsyncSessionLocal() as db:
        passos = await flow_engine.simular(
            db, tenant, _bot(), ["oi", "quero falar com um atendente"]
        )
    assert passos[-1]["encaminha"] is True


async def test_simula_rascunho_sem_publicar(tenant) -> None:
    """Conferir uma alteração antes de ela ir ao ar — sem ela, editar é editar no escuro."""
    rascunho = copy.deepcopy(GRAFO)
    rascunho["nos"]["saudacao"]["texto"] = "Boa tarde! Aqui é o Salão Exemplo."

    async with AsyncSessionLocal() as db:
        passos = await flow_engine.simular(db, tenant, _bot(), ["oi"], grafo=rascunho)
        publicado = await flow_store.carregar(db, tenant)

    assert "Boa tarde!" in passos[0]["bolhas"][0]
    # O rascunho não vazou para o que está no ar.
    assert publicado["grafo"]["nos"]["saudacao"]["texto"].startswith("Oi! 👋")


async def test_simulacao_de_roteiro_quebrado_nao_explode(tenant) -> None:
    """Quem está editando precisa ver o defeito, não um 500."""
    quebrado = copy.deepcopy(GRAFO)
    quebrado["nos"]["menu_principal"]["opcoes"][0]["proximo"] = "evaporou"

    async with AsyncSessionLocal() as db:
        passos = await flow_engine.simular(
            db, tenant, _bot(), ["oi", "1"], grafo=quebrado
        )

    assert passos[-1]["encaminha"] is True
    assert any("travaria aqui" in n for n in passos[-1]["notas"])


# ─────────────────────────── quem manda no roteiro ───────────────────────────


async def test_assumir_no_painel_para_o_sincronizador(tenant) -> None:
    async with AsyncSessionLocal() as db:
        assert (await flow_store.carregar(db, tenant))[
            "gerenciado_por_arquivo"
        ] is False
        await flow_store.salvar(db, tenant, GRAFO, "Teste", gerenciado_por_arquivo=True)
        await db.commit()
        assert (await flow_store.carregar(db, tenant))["gerenciado_por_arquivo"] is True

        assert await flow_store.assumir_no_painel(db, tenant) is True
        await db.commit()
        assert (await flow_store.carregar(db, tenant))[
            "gerenciado_por_arquivo"
        ] is False


async def test_publicar_sobe_a_versao(tenant) -> None:
    """A versão é o que a nota interna cita quando um nó some no meio de uma conversa."""
    async with AsyncSessionLocal() as db:
        antes = (await flow_store.carregar(db, tenant))["versao"]
        depois = await flow_store.salvar(db, tenant, GRAFO, "Teste")
        await db.commit()
    assert depois == antes + 1


# ───────────────────── o roteiro versionado, frase por frase ─────────────────────
#
# O casamento de opção é por SUBSTRING, e a PRIMEIRA opção que casa vence. Isso é o que
# faz "queria ver as fotos" cair na opção certa — e é também a armadilha: um sinônimo
# curto numa opção de cima engole a intenção de uma opção de baixo, em silêncio. Foi
# assim que `"valor"` na opção 1 sequestrava "o que está incluso no VALOR?", mandando
# para a pergunta de data quem queria saber o que vem na locação.
#
# Nenhuma lógica de casamento é reimplementada aqui: quem responde é o motor, pelo
# simulador. `sinonimos` é editável pelo painel, então esta tabela trava só o padrão que
# o repositório publica — que é justamente o que o cliente novo recebe.

FRASES = [
    # (o que o cliente manda depois de "oi", em qual caixa tem de parar)
    (["qual o valor?"], "perguntar_data"),
    (["me passa o valor"], "perguntar_data"),
    (["quanto custa alugar?"], "perguntar_data"),
    (["queria saber os valores"], "perguntar_data"),
    (["tem data livre em agosto?"], "perguntar_data"),
    (["quero um orçamento"], "perguntar_data"),
    # As duas que o `"valor"` cru levava para o lado errado.
    (["o que ta incluso no valor?"], "menu_duvidas"),
    (["o valor inclui buffet?"], "menu_duvidas"),
    (["cabe quantas pessoas?"], "menu_duvidas"),
    (["tem estacionamento?"], "menu_duvidas"),
    (["tem piscina?"], "menu_duvidas"),
    (["até que horas pode ter som?"], "menu_duvidas"),
    (["qual o endereço de vocês?"], "menu_duvidas"),
    (["queria ver as fotos do espaço"], "handoff_fotos"),  # galeria vazia neste tenant
    (["posso visitar antes de fechar?"], "handoff_visita"),
    (["quero agendar uma visita"], "handoff_visita"),
    (["quero cancelar minha reserva"], "handoff_cliente"),
    (["preciso remarcar minha festa"], "handoff_cliente"),
    # Dentro do submenu de dúvidas, onde os assuntos competem entre si.
    (["3", "o buffet está incluso?"], "menu_apos_duvida"),
    (["3", "pode levar buffet de fora?"], "menu_apos_duvida"),
    (["3", "quantas pessoas cabem?"], "menu_apos_duvida"),
    # O número acompanhado de palavra. "opção 3" caiu em "não entendi" no primeiro teste
    # com número real, duas vezes seguidas — é das formas mais naturais de responder um
    # menu numerado, e o casamento estrito exigia a mensagem inteira em dígitos.
    (["opção 3"], "menu_duvidas"),
    (["a opção 3"], "menu_duvidas"),
    (["op 3"], "menu_duvidas"),
    (["quero a 3"], "menu_duvidas"),
    (["3."], "menu_duvidas"),
    (["3️⃣"], "menu_duvidas"),
    (["3 por favor"], "menu_duvidas"),
    # E o limite: aqui o "2" não é escolha de menu. O roteiro fica onde está e repete as
    # opções (é a primeira resposta inválida); o que ele NÃO pode é mandar para as fotos.
    (["quero para 2 pessoas em julho"], "menu_principal"),
    # Data e contagem curtas — um número só, poucas palavras. A primeira versão do número
    # solto aceitava qualquer frase de até três palavras e mandava "dia 5" e "5 de
    # outubro" para "Já tenho uma reserva" (opção 5), com o assunto errado na nota.
    (["dia 5"], "menu_principal"),
    (["5 de outubro"], "menu_principal"),
    (["sábado dia 3"], "menu_principal"),
    (["seremos 4"], "menu_principal"),
    # Insistindo, chama gente — a saída de emergência de sempre.
    (["quero para 2 pessoas em julho", "e aí?"], "chamar_equipe"),
]


@pytest.mark.parametrize("mensagens,esperado", FRASES)
async def test_frase_do_cliente_cai_na_caixa_certa(
    tenant, mensagens: list[str], esperado: str
) -> None:
    async with AsyncSessionLocal() as db:
        passos = await flow_engine.simular(
            db, tenant, _bot(), ["oi", *mensagens], grafo=GRAFO
        )
    assert passos[-1]["no"] == esperado, [p["no"] for p in passos]


async def test_submenu_de_duvidas_responde_o_assunto_pedido(tenant) -> None:
    """Não basta parar na caixa certa: a resposta tem de ser a do assunto perguntado."""
    casos = [
        ("o buffet está incluso?", "Não entram na locação"),
        ("pode levar buffet de fora?", "Buffet externo é liberado"),
        ("tem churrasqueira?", "churrasqueira na área gourmet"),
        ("qual a lotação máxima?", "350 pessoas"),
        ("onde fica?", "Rua Exemplo"),
    ]
    for frase, trecho in casos:
        async with AsyncSessionLocal() as db:
            passos = await flow_engine.simular(
                db, tenant, _bot(), ["oi", "3", frase], grafo=GRAFO
            )
        assert trecho in "\n".join(passos[-1]["bolhas"]), frase
