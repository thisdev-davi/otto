"""Avaliação offline do agente: casos fixos, modelo real, checagens determinísticas.

    python -m evals.rodar --provider anthropic --model <modelo> --vezes 3

A chave vem do ambiente, como no resto do Otto (`LLM_API_KEY`/`ANTHROPIC_API_KEY`). O
resultado vai para `evals/RESULTADO.md`.

O agente recebe o MESMO prompt de produção: `conteudo/persona.md`, `conteudo/conhecimento.md`
e o bloco de agenda montado por `salao_contexto.bloco_agenda` — só as três leituras de
banco que ele faz são desviadas para a agenda de `casos.json`. O formato do bloco, os
rótulos de motivo e as regras de preço são os de produção, não uma cópia. Fica de fora o
RAG de `conteudo/base/` (exige embeddings) e a memória de conversas anteriores.
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from app.ai import llm
from app.core.config import settings
from app.services import salao_agenda, salao_contexto
from evals.checagens import avaliar

RAIZ = Path(__file__).resolve().parent.parent
CASOS = Path(__file__).with_name("casos.json")
RESULTADO = Path(__file__).with_name("RESULTADO.md")


def _agenda_falsa(agenda: dict[str, dict]):
    """As três leituras de banco do `bloco_agenda`, servidas pela fixture."""
    linhas = {date.fromisoformat(k): v for k, v in agenda.items()}

    async def tem_agenda(db, tenant_id) -> bool:
        return True

    async def consultar(db, tenant_id, datas, hoje=None) -> dict:
        return {d: linhas[d] for d in datas if d in linhas}

    async def proximas_livres(db, tenant_id, limite=6, a_partir_de=None) -> list[dict]:
        livres = sorted(
            d
            for d, v in linhas.items()
            if v["motivo"] == salao_agenda.MOTIVO_OK and d >= (a_partir_de or d)
        )
        return [
            {"data": d, "preco_centavos": linhas[d]["preco_centavos"]}
            for d in livres[:limite]
        ]

    return (
        patch.object(salao_contexto, "_tem_agenda", tem_agenda),
        patch.object(salao_agenda, "consultar", consultar),
        patch.object(salao_agenda, "proximas_livres", proximas_livres),
    )


async def _turno(caso: dict, ctx: dict, args) -> dict | None:
    historico = [{"role": "user", "content": m} for m in caso["mensagens"]]
    a, b, c = _agenda_falsa(ctx["agenda"])
    with a, b, c:
        bloco = await salao_contexto.bloco_agenda(
            None, uuid.uuid4(), caso["mensagens"][-1], date.fromisoformat(ctx["hoje"])
        )
    return await llm.agent_turn(
        persona=ctx["persona"],
        state={},
        history=historico,
        knowledge=ctx["conhecimento"] + bloco,
        provider=args.provider,
        model=args.model,
    )


def _relatorio(linhas: list[dict], args, modelo: str) -> str:
    total = sum(len(linha["rodadas"]) for linha in linhas)
    passou = sum(r["ok"] for linha in linhas for r in linha["rodadas"])
    saida = [
        "# Avaliação offline do agente",
        "",
        f"- **Modelo:** `{args.provider}/{modelo}`",
        f"- **Data:** {datetime.now():%d/%m/%Y %H:%M}",
        f"- **Placar:** {passou}/{total} turnos aprovados "
        f"({len(linhas)} casos × {args.vezes} rodadas)",
        "",
        "Gerado por `python -m evals.rodar`. Casos em `evals/casos.json`, checagens em "
        "`evals/checagens.py`.",
        "",
        "| Caso | O que trava | Aprovado | Falhas |",
        "|---|---|---|---|",
    ]
    for linha in linhas:
        ok = sum(r["ok"] for r in linha["rodadas"])
        falhas = "; ".join(
            sorted({f for r in linha["rodadas"] for f in r["falhas"]})
        ).replace("|", "\\|")
        saida.append(
            f"| `{linha['id']}` | {linha['descricao']} | {ok}/{len(linha['rodadas'])} "
            f"| {falhas or '—'} |"
        )
    return "\n".join(saida) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default=settings.LLM_PROVIDER or "stub")
    parser.add_argument("--model", default="")
    parser.add_argument("--vezes", type=int, default=3, help="rodadas por caso")
    parser.add_argument("--so", default="", help="roda só o caso com este id")
    args = parser.parse_args()

    dados = json.loads(CASOS.read_text())
    ctx = {
        "hoje": dados["hoje"],
        "agenda": dados["agenda"],
        "persona": (RAIZ / "conteudo/persona.md").read_text(),
        "conhecimento": (RAIZ / "conteudo/conhecimento.md").read_text(),
    }
    casos = [c for c in dados["casos"] if not args.so or c["id"] == args.so]

    linhas = []
    for caso in casos:
        rodadas = []
        for _ in range(args.vezes):
            decisao = await _turno(caso, ctx, args)
            falhas = avaliar(decisao, ctx, caso["checagens"])
            rodadas.append({"ok": not falhas, "falhas": falhas})
            resposta = (decisao or {}).get("reply", "")
            print(f"{'✓' if not falhas else '✗'} {caso['id']}: {resposta[:100]!r}")
            for falha in falhas:
                print(f"    {falha}")
        linhas.append({**caso, "rodadas": rodadas})

    modelo = args.model or llm._default_model(args.provider)
    RESULTADO.write_text(_relatorio(linhas, args, modelo))
    print(f"\nRelatório em {RESULTADO.relative_to(RAIZ)}")
    return 0 if all(r["ok"] for linha in linhas for r in linha["rodadas"]) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
