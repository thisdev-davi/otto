"""As checagens da avaliação offline (`evals/`), sem rede e sem modelo.

Se uma checagem aprova o que devia reprovar, o placar do eval vira propaganda.
"""

import json
from pathlib import Path

from evals.checagens import avaliar

DADOS = json.loads((Path(__file__).parent.parent / "evals/casos.json").read_text())
CTX = {"hoje": DADOS["hoje"], "agenda": DADOS["agenda"]}


def _decisao(reply: str = "", status: str = "gathering", **captured) -> dict:
    return {"reply": reply, "status": status, "captured": captured}


def test_preco_da_agenda_passa_e_inventado_reprova() -> None:
    assert (
        avaliar(
            _decisao("O dia 12/12 sai por R$ 5.200,00"), CTX, [["precos_da_agenda"]]
        )
        == []
    )
    falhas = avaliar(
        _decisao("Com desconto fica R$ 2.600,00"), CTX, [["precos_da_agenda"]]
    )
    assert falhas and "2.600,00" in falhas[0]


def test_preco_de_data_ocupada_nao_conta_como_valido() -> None:
    """19/12 tem preço cadastrado, mas está ocupada: o valor não pode ser dito."""
    assert avaliar(_decisao("R$ 5.200,00"), CTX, [["precos_da_agenda"]]) == []
    assert avaliar(_decisao("R$ 4.800,00"), CTX, [["precos_da_agenda"]])  # bloqueada


def test_preco_sem_centavos_e_normalizado() -> None:
    assert avaliar(_decisao("Fica R$ 100"), CTX, [["precos_da_agenda"]])


def test_sem_valor_para_olha_a_linha_da_data() -> None:
    texto = "O 26/12 eu confirmo com a equipe.|||Livres: *12/12* — R$ 5.200,00"
    assert avaliar(_decisao(texto), CTX, [["sem_valor_para", "26/12"]]) == []
    assert avaliar(
        _decisao("26/12 fica R$ 5.200,00"), CTX, [["sem_valor_para", "26/12"]]
    )


def test_confirmacao_e_negacao() -> None:
    chk = [["nao_confirma_reserva"]]
    assert avaliar(_decisao("Sua reserva está confirmada!"), CTX, chk)
    assert avaliar(_decisao("Pronto, confirmei pra você"), CTX, chk)
    assert (
        avaliar(
            _decisao("A data ainda não está confirmada; a equipe confirma."), CTX, chk
        )
        == []
    )


def test_encaminha_pelo_status() -> None:
    assert avaliar(_decisao(status="needs_human"), CTX, [["encaminha"]]) == []
    assert avaliar(_decisao(status="gathering"), CTX, [["encaminha"]])


def test_pre_reserva_le_as_chaves_reais_da_persona() -> None:
    pede = _decisao(_acao_pre_reserva="2026-12-12")
    assert avaliar(pede, CTX, [["pre_reserva_em", "2026-12-12"]]) == []
    assert avaliar(pede, CTX, [["sem_pre_reserva"]])
    # A checagem não pode consumir a chave da decisão que outra checagem ainda vai ler.
    assert pede["captured"] == {"_acao_pre_reserva": "2026-12-12"}
    assert avaliar(_decisao(), CTX, [["sem_pre_reserva"]]) == []


def test_modelo_sem_resposta_reprova() -> None:
    assert avaliar(None, CTX, [["precos_da_agenda"]])


def test_casos_so_usam_checagens_que_existem() -> None:
    from evals.checagens import CHECAGENS

    for caso in DADOS["casos"]:
        for nome, *_ in caso["checagens"]:
            assert nome in CHECAGENS, (caso["id"], nome)
