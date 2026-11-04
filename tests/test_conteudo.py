"""O conteúdo do agente, validado como código.

Este arquivo existe por um motivo específico: **duas features de produto são prosa**.
Envio de fotos e pré-reserva só acontecem porque a persona manda o modelo emitir
`_acao_fotos` e `_acao_pre_reserva` no `captured` (`app/services/salao_acoes.py`). Apagar
esse parágrafo desliga os dois recursos sem erro em log, sem exceção e sem teste falhando
— era o único ponto do sistema onde uma edição de texto derrubava funcionalidade em
silêncio absoluto.

Roda sem banco e sem rede: é só leitura de arquivo, então entra no `pytest` normal.
"""

import importlib.util
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# O sincronizador vive em `scripts/` e não é um pacote importável; carregá-lo pelo caminho
# mantém UMA implementação das validações, em vez de duplicá-las aqui e vê-las divergir.
_spec = importlib.util.spec_from_file_location(
    "sincronizar_conteudo", RAIZ / "scripts" / "sincronizar_conteudo.py"
)
sincronizar_conteudo = importlib.util.module_from_spec(_spec)
sys.modules["sincronizar_conteudo"] = sincronizar_conteudo
_spec.loader.exec_module(sincronizar_conteudo)


def test_conteudo_passa_nas_validacoes() -> None:
    """O portão. Se isto falha, a mensagem do erro já diz o que consertar."""
    erros = sincronizar_conteudo.validar()
    assert erros == [], "\n".join(erros)


def test_persona_mantem_o_protocolo_de_acoes() -> None:
    """A trava que motivou o arquivo — explícita, para não morrer numa refatoração."""
    persona = (RAIZ / "conteudo" / "persona.md").read_text(encoding="utf-8")
    for chave in ("_acao_fotos", "_acao_pre_reserva"):
        assert chave in persona, (
            f"{chave} sumiu da persona. Sem essa chave o agente para de "
            "usar o recurso correspondente, e nada mais no sistema acusa."
        )
    # O formato da data é contrato com `salao_acoes._parse_data`.
    assert "AAAA-MM-DD" in persona


def test_preco_nao_vaza_para_o_conteudo_estatico() -> None:
    """Preço tem uma fonte só: a agenda, em tempo real.

    Um valor escrito no conhecimento ou num documento compete com o bloco da agenda
    dentro do mesmo prompt — e é o texto estático que estará errado no dia seguinte.
    """
    alvos = [RAIZ / "conteudo" / "conhecimento.md"]
    alvos += [
        p
        for p in (RAIZ / "conteudo" / "base").glob("*.md")
        if not p.name.startswith("_")
    ]
    for caminho in alvos:
        texto = caminho.read_text(encoding="utf-8")
        achado = sincronizar_conteudo.PADRAO_DINHEIRO.search(texto)
        assert achado is None, (
            f"{caminho.name} cita valor em dinheiro ({achado.group(0)!r})."
        )


def test_documentos_tem_titulo() -> None:
    """O título é colado na frente de cada trecho recuperado (`ai_context.py`)."""
    docs = sincronizar_conteudo.documentos()
    assert docs, "conteudo/base/ não tem nenhum documento indexável"
    for source, title, corpo in docs:
        assert title, f"base/{source}.md não tem `title` no front-matter"
        assert corpo.strip(), f"base/{source}.md está vazio"


def test_arquivo_com_underscore_nao_e_indexado() -> None:
    """`_LEIA-ME.md` explica a pasta para humanos; virar vetor seria ruído na busca."""
    sources = {s for s, _, _ in sincronizar_conteudo.documentos()}
    assert not any(s.startswith("_") for s in sources)
    assert (RAIZ / "conteudo" / "base" / "_LEIA-ME.md").is_file()


# ─────────────────────────── o roteiro do atendimento por fluxo ───────────────────────────


def test_fluxo_json_e_publicavel() -> None:
    """`conteudo/fluxo.json` passa no MESMO validador de `PUT /fluxo`.

    O sincronizador publica este arquivo em `bot_flows`, e um roteiro quebrado só daria
    sinal quando um cliente real caísse nele. Aqui dá sinal no `pytest`.
    """
    from app.flow import schema  # noqa: PLC0415
    from app.services import salao_fluxo  # noqa: PLC0415, F401  registra agenda e acao

    grafo = sincronizar_conteudo.carregar_fluxo()
    assert grafo is not None, "conteudo/fluxo.json sumiu"
    assert schema.validar(grafo) == []


def test_validar_reprova_fluxo_quebrado(tmp_path, monkeypatch) -> None:
    """O `--check` do sincronizador é o portão: fluxo torto não chega ao banco."""
    torto = tmp_path / "fluxo.json"
    torto.write_text('{"inicio": "a", "nos": {}}', encoding="utf-8")
    monkeypatch.setattr(sincronizar_conteudo, "FLUXO", torto)
    assert any("fluxo.json" in e for e in sincronizar_conteudo.validar())

    torto.write_text("{isto nao e json", encoding="utf-8")
    assert any("não é JSON válido" in e for e in sincronizar_conteudo.validar())
