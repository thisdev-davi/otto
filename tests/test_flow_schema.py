"""O validador do roteiro: o que ele reprova é o que nunca chega a um cliente.

Este arquivo é a rede que sustenta a promessa "o fluxo que você desenha é o fluxo que
roda". O mesmo `validar()` roda em três lugares — `PUT /fluxo`, o sincronizador de
conteúdo e aqui —, então o que passar por ele passa em produção.

Sem banco de propósito: o validador é puro, e teste puro é teste que ninguém desliga.
"""

import copy
import json
from pathlib import Path

import pytest

from app.flow import schema
from app.services import salao_fluxo  # noqa: F401  registra os nós `agenda` e `acao`

FLUXO_PADRAO = Path(__file__).resolve().parent.parent / "conteudo" / "fluxo.json"


def _grafo_padrao() -> dict:
    """Cópia nova a cada chamada: `aplicar_textos` recebe o grafo publicado, e um
    teste que mutasse a constante contaminaria os seguintes."""
    return json.loads(FLUXO_PADRAO.read_text(encoding="utf-8"))


@pytest.fixture
def grafo() -> dict:
    return json.loads(FLUXO_PADRAO.read_text(encoding="utf-8"))


def test_o_roteiro_versionado_no_repo_e_publicavel(grafo: dict) -> None:
    """Se este cair, o `conteudo/fluxo.json` do repositório não pode ir ao ar."""
    assert schema.validar(grafo) == []


def test_todo_no_do_roteiro_tem_posicao(grafo: dict) -> None:
    """A posição mora no dado, não num algoritmo de layout — é o que a tela desenha."""
    sem_pos = [nid for nid, no in grafo["nos"].items() if "pos" not in no]
    assert sem_pos == []


def test_preco_escrito_no_roteiro_e_reprovado(grafo: dict) -> None:
    """Regra 4: preço tem uma fonte só, a agenda em tempo real.

    Escrito no roteiro vira um segundo preço competindo com ela — e é o texto estático que
    estará errado no dia em que o dono vender uma quinta-feira.
    """
    g = copy.deepcopy(grafo)
    g["nos"]["data_livre"]["texto"] = "O valor dessa data é R$ 4.200,00"
    assert any("dinheiro" in e for e in schema.validar(g))

    g = copy.deepcopy(grafo)
    g["nos"]["menu_principal"]["opcoes"][0]["rotulo"] = "Ver preços em reais"
    assert any("dinheiro" in e for e in schema.validar(g))


def test_agenda_precisa_tratar_os_seis_motivos(grafo: dict) -> None:
    """Motivo sem ramo é o caminho por onde um caso cai onde não devia.

    `sem_preco` sem saída própria acabaria caindo no ramo de data livre — que é
    exatamente a mensagem que informa um valor.
    """
    for motivo in salao_fluxo.SAIDAS_AGENDA:
        g = copy.deepcopy(grafo)
        del g["nos"]["consultar_agenda"]["saidas"][motivo]
        erros = schema.validar(g)
        assert any(f"falta a saída `{motivo}`" in e for e in erros), motivo


def test_saida_para_no_inexistente(grafo: dict) -> None:
    g = copy.deepcopy(grafo)
    g["nos"]["menu_principal"]["opcoes"][0]["proximo"] = "nao_existe"
    assert any("nao_existe" in e for e in schema.validar(g))


def test_no_orfao_e_reprovado(grafo: dict) -> None:
    """Nó inalcançável é quase sempre uma ligação esquecida, não um nó a mais."""
    g = copy.deepcopy(grafo)
    g["nos"]["perdido"] = {"tipo": "mensagem", "texto": "oi", "proximo": "fim"}
    assert any("perdido: nenhum caminho leva até aqui" in e for e in schema.validar(g))


def test_ciclo_sem_no_de_espera_e_reprovado() -> None:
    """Volta infinita dentro de um turno só. O teto do motor salva o cliente; o
    validador é quem impede isso de chegar em produção."""
    g = {
        "inicio": "a",
        "config": {"no_handoff": "h"},
        "nos": {
            "a": {"tipo": "mensagem", "texto": "oi", "proximo": "b"},
            "b": {"tipo": "mensagem", "texto": "e ai", "proximo": "a"},
            "h": {"tipo": "handoff"},
        },
    }
    assert any("ciclo sem nó de espera" in e for e in schema.validar(g))


def test_ciclo_que_passa_por_menu_e_legitimo() -> None:
    """O MESMO desenho com um menu no meio é um roteiro normal: o menu consome uma
    mensagem por volta, então não há giro livre. Reprovar isto proibiria "quer ver outra
    data?", que é metade do roteiro do salão."""
    g = {
        "inicio": "a",
        "config": {"no_handoff": "h"},
        "nos": {
            "a": {"tipo": "mensagem", "texto": "oi", "proximo": "m"},
            "m": {
                "tipo": "menu",
                "texto": "e ai?",
                "opcoes": [
                    {"rotulo": "de novo", "proximo": "a"},
                    {"rotulo": "sair", "proximo": "h"},
                ],
            },
            "h": {"tipo": "handoff"},
        },
    }
    assert schema.validar(g) == []


def test_no_handoff_obrigatorio_e_do_tipo_certo(grafo: dict) -> None:
    """É para onde vai quem sai do roteiro: sem ele, sair do roteiro é ficar sem resposta."""
    g = copy.deepcopy(grafo)
    del g["config"]["no_handoff"]
    assert any("no_handoff" in e for e in schema.validar(g))

    g = copy.deepcopy(grafo)
    g["config"]["no_handoff"] = "saudacao"
    assert any("precisa ser um nó do tipo `handoff`" in e for e in schema.validar(g))


def test_tipo_desconhecido_nao_passa_em_silencio(grafo: dict) -> None:
    """Inclusive quando o derivado esquece de importar `salao_fluxo`: falhar aqui é
    barulhento, e é o oposto de um nó que não executa nada em produção."""
    g = copy.deepcopy(grafo)
    g["nos"]["saudacao"]["tipo"] = "carrossel"
    assert any("tipo desconhecido" in e for e in schema.validar(g))


def test_defeitos_de_forma(grafo: dict) -> None:
    casos = [
        (
            lambda g: g["nos"]["menu_reserva"].__setitem__("opcoes", []),
            "menu sem `opcoes`",
        ),
        (
            lambda g: g["nos"]["perguntar_convidados"].__setitem__("validacao", "cpf"),
            "`validacao` 'cpf' não existe",
        ),
        (lambda g: g["nos"]["saudacao"].__setitem__("texto", ""), "`texto` vazio"),
        (
            lambda g: g["nos"]["saudacao"].__setitem__("pos", "meio"),
            "`pos` precisa ser",
        ),
        (lambda g: g.__setitem__("inicio", "nunca_existiu"), "`inicio` aponta"),
        (
            lambda g: g["nos"]["perguntar_nome"].pop("campo"),
            "falta o campo obrigatório `campo`",
        ),
    ]
    for muta, esperado in casos:
        g = copy.deepcopy(grafo)
        muta(g)
        assert any(esperado in e for e in schema.validar(g)), esperado


def test_grafo_vazio_ou_torto_nao_explode() -> None:
    """O validador recebe JSON de quem estiver editando: nunca pode levantar exceção."""
    for entrada in ({}, {"nos": {}}, {"nos": []}, [], "texto", None):
        erros = schema.validar(entrada)
        assert erros and all(isinstance(e, str) for e in erros)


# ─────────────────────────── a trava da edição pelo painel ───────────────────────────


def test_texto_e_rotulo_sao_editaveis() -> None:
    novo, erros = schema.aplicar_textos(
        _grafo_padrao(),
        {
            "nos": {
                "saudacao": {"texto": "Oi! 👋 Aqui é a Bia."},
                "menu_reserva": {"opcoes": [{"rotulo": "Pode segurar!"}, {}, {}, {}]},
                "chamar_equipe_data": {"resumo": "Data fora da agenda."},
            },
            "config": {"nao_entendi": "Não peguei 😅"},
        },
    )
    assert erros == []
    assert novo["nos"]["saudacao"]["texto"] == "Oi! 👋 Aqui é a Bia."
    assert novo["nos"]["menu_reserva"]["opcoes"][0]["rotulo"] == "Pode segurar!"
    assert novo["config"]["nao_entendi"] == "Não peguei 😅"
    # E o original não foi mutado: quem publica é quem chama, com o retorno.
    assert _grafo_padrao()["nos"]["saudacao"]["texto"].startswith(
        "Oi! 👋 Aqui é a Bia, do Salão"
    )


def test_estrutura_nao_passa_pelo_painel() -> None:
    """A lista dos jeitos de quebrar um roteiro que o painel não oferece — e que também
    não aceita se alguém montar a requisição na mão."""
    casos = [
        ({"saudacao": {"proximo": "fim"}}, "não é texto"),
        ({"saudacao": {"tipo": "menu"}}, "não é texto"),
        ({"consultar_agenda": {"saidas": {"livre": "fim"}}}, "não é texto"),
        ({"perguntar_nome": {"campo": "outro"}}, "não é texto"),
        ({"perguntar_convidados": {"validacao": "cpf"}}, "não é texto"),
        ({"saudacao": {"pos": [0, 0]}}, "não é texto"),
        ({"menu_reserva": {"opcoes": [{"rotulo": "a"}]}}, "não quantas são"),
        (
            {"menu_reserva": {"opcoes": [{"proximo": "fim"}, {}, {}, {}]}},
            "não é texto editável",
        ),
        ({"nao_existe": {"texto": "oi"}}, "não existe no roteiro publicado"),
    ]
    for edicao, esperado in casos:
        _, erros = schema.aplicar_textos(_grafo_padrao(), {"nos": edicao})
        assert erros, f"deveria recusar: {edicao}"
        assert any(esperado in e for e in erros), (edicao, erros)


def test_preco_escrito_pelo_painel_e_reprovado() -> None:
    """A regra 4 vale para quem edita pelo painel exatamente como vale para o arquivo."""
    _, erros = schema.aplicar_textos(
        _grafo_padrao(), {"nos": {"data_livre": {"texto": "Fica por R$ 3.900,00"}}}
    )
    assert any("dinheiro" in e for e in erros)


def test_edicao_que_esvazia_um_texto_e_reprovada() -> None:
    """Bolha em branco chega ao cliente como mensagem vazia."""
    _, erros = schema.aplicar_textos(
        _grafo_padrao(), {"nos": {"saudacao": {"texto": "  "}}}
    )
    assert any("texto` vazio" in e for e in erros)


def test_config_so_aceita_o_que_e_texto() -> None:
    _, erros = schema.aplicar_textos(_grafo_padrao(), {"config": {"no_handoff": "fim"}})
    assert any("não é um texto editável" in e for e in erros)
