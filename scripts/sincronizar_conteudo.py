#!/usr/bin/env python3
"""Sincroniza `conteudo/` para o banco: persona, conhecimento e base vetorial.

Os arquivos são a FONTE DA VERDADE; o banco é cache. O que se lê em `conteudo/persona.md`
é exatamente o que o modelo recebe, byte a byte — por isso o arquivo não tem título,
índice nem comentário para humano: cada caractere é enviado em toda mensagem trocada.

    python3 scripts/sincronizar_conteudo.py --check              # só valida, sem banco
    python3 scripts/sincronizar_conteudo.py --conta "Meu Salão" --dry-run
    python3 scripts/sincronizar_conteudo.py --conta "Meu Salão"

Idempotente: `kb.ingest_document` já é idempotente por `(tenant_id, source)`, então rodar
duas vezes não duplica trecho nenhum. Source cujo arquivo sumiu é removida.

**Por que existe:** duas features de produto — envio de fotos e pré-reserva — são prosa
dentro da persona (`docs/guias/GUIA-PERSONA-SALAO.md`). Enquanto isso morava num `<textarea>`, apagar
um parágrafo desligava as duas sem erro em log, sem histórico e sem revisão. Em arquivo,
isso vira diff de PR — e a validação abaixo reprova antes de chegar ao banco.
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

CONTEUDO = RAIZ / "conteudo"
PERSONA = CONTEUDO / "persona.md"
CONHECIMENTO = CONTEUDO / "conhecimento.md"
BASE = CONTEUDO / "base"
# O roteiro do atendimento por fluxo. Opcional: um tenant que só usa o agente de IA
# não precisa dele, e a ausência do arquivo não é erro.
FLUXO = CONTEUDO / "fluxo.json"

# Tetos folgados sobre os alvos do `docs/guias/GUIA-CONTEUDO-AGENTE.md` (3.500 / 1.200). Só disparam
# em desvio real — e o custo destes dois campos é cobrado em TODA mensagem, o dia inteiro.
MAX_PERSONA = 4000
MAX_CONHECIMENTO = 1500

# As duas chaves que fazem as ações do agente existirem (`app/services/salao_acoes.py`).
CHAVES_DE_ACAO = ("_acao_fotos", "_acao_pre_reserva")

# Preço tem uma fonte só: a agenda, em tempo real. Escrito no conteúdo, vira um segundo
# preço competindo com ela — e é o texto estático que estará errado amanhã.
PADRAO_DINHEIRO = re.compile(r"R\$|\breais\b", re.IGNORECASE)

# Placeholder não preenchido: `[NOME DO SALÃO]` ou `[permite / não permite]` chegando ao
# cliente é pior que agenda vazia. Qualquer colchete conta — tanto o campo a preencher
# quanto o "escolha uma opção" do molde —, exceto link markdown `[texto](url)`, que é
# legítimo no `_LEIA-ME.md` (esse nem é indexado, mas a regra vale para todos).
# Bloqueia a escrita no banco, mas não o `--check` nem o `--dry-run`: o repositório é um
# molde, e ter placeholder é o estado normal dele.
PADRAO_PLACEHOLDER = re.compile(r"\[[^\]\n]{2,}\](?!\()")


def separar_frontmatter(texto: str) -> tuple[dict[str, str], str]:
    """Front-matter YAML raso (só `chave: valor`) + corpo. Sem dependência nova."""
    if not texto.startswith("---"):
        return {}, texto
    partes = texto.split("---", 2)
    if len(partes) < 3:
        return {}, texto
    meta: dict[str, str] = {}
    for linha in partes[1].strip().splitlines():
        if ":" in linha:
            chave, _, valor = linha.partition(":")
            meta[chave.strip()] = valor.strip().strip("\"'")
    return meta, partes[2].lstrip("\n")


def documentos() -> list[tuple[str, str, str]]:
    """`(source, title, corpo)` de cada `base/*.md`. Arquivo com `_` não é indexado."""
    if not BASE.is_dir():
        return []
    achados = []
    for caminho in sorted(BASE.glob("*.md")):
        if caminho.name.startswith("_"):
            continue
        meta, corpo = separar_frontmatter(caminho.read_text(encoding="utf-8"))
        achados.append((caminho.stem, meta.get("title", ""), corpo.strip()))
    return achados


def placeholders_pendentes() -> list[str]:
    """Placeholders `[ASSIM]` que ainda não foram preenchidos, por arquivo.

    Separado de `validar` de propósito: o repositório é um MOLDE, então ter placeholder é
    o estado normal dele. Isto só barra a escrita no banco — `--check` e `--dry-run`
    continuam funcionando, que é o que permite revisar o molde no CI.
    """
    alvos: list[tuple[str, str]] = []
    if PERSONA.is_file():
        alvos.append(("persona.md", PERSONA.read_text(encoding="utf-8")))
    if CONHECIMENTO.is_file():
        alvos.append(("conhecimento.md", CONHECIMENTO.read_text(encoding="utf-8")))
    alvos += [(f"base/{s}.md", c) for s, _, c in documentos()]

    pendentes = []
    for nome, texto in alvos:
        achados = sorted(set(PADRAO_PLACEHOLDER.findall(texto)))
        if achados:
            pendentes.append(f"{nome}: {', '.join(achados[:3])}…")
    return pendentes


def validar() -> list[str]:
    """Devolve a lista de problemas. Vazia = conteúdo está bem formado."""
    erros: list[str] = []

    if not PERSONA.is_file():
        return [f"{PERSONA.relative_to(RAIZ)} não existe."]
    persona = PERSONA.read_text(encoding="utf-8")

    faltando = [c for c in CHAVES_DE_ACAO if c not in persona]
    if faltando:
        erros.append(
            f"persona.md não menciona {', '.join(faltando)} — sem essas chaves o agente "
            "para de enviar fotos e de segurar datas, SEM erro em log nenhum."
        )
    if "O QUE VOCÊ NUNCA FAZ" not in persona:
        erros.append(
            "persona.md não tem a seção 'O QUE VOCÊ NUNCA FAZ'. Toda proibição mora na "
            "persona: a busca semântica não garante trazer uma regra na hora certa."
        )
    if len(persona) > MAX_PERSONA:
        erros.append(
            f"persona.md tem {len(persona)} caracteres (teto {MAX_PERSONA}). Ela é "
            "enviada ao modelo em toda mensagem — o excedente vira documento."
        )

    if not CONHECIMENTO.is_file():
        erros.append(f"{CONHECIMENTO.relative_to(RAIZ)} não existe.")
    else:
        conhecimento = CONHECIMENTO.read_text(encoding="utf-8")
        if len(conhecimento) > MAX_CONHECIMENTO:
            erros.append(
                f"conhecimento.md tem {len(conhecimento)} caracteres "
                f"(teto {MAX_CONHECIMENTO}). Ele é cobrado em toda mensagem."
            )
        if PADRAO_DINHEIRO.search(conhecimento):
            erros.append(
                "conhecimento.md cita valor em dinheiro. Preço vem da agenda, em tempo "
                "real; escrito aqui vira um segundo preço que estará errado amanhã."
            )

    # `base/` vazia NÃO é erro: RAG é opcional (`use_rag` nasce desligado), e um salão
    # pode operar bem só com persona e conhecimento até ter conteúdo longo para indexar.
    docs = documentos()
    for source, title, corpo in docs:
        if not title:
            erros.append(
                f"base/{source}.md não tem `title` no front-matter. O título é colado na "
                "frente de cada trecho recuperado, então o modelo o lê em toda resposta."
            )
        if not corpo:
            erros.append(f"base/{source}.md está vazio depois do front-matter.")
        if PADRAO_DINHEIRO.search(corpo):
            erros.append(
                f"base/{source}.md cita valor em dinheiro. Preço vem só da agenda."
            )

    erros += _erros_do_fluxo()
    return erros


def carregar_fluxo() -> dict | None:
    """O grafo de `conteudo/fluxo.json`, ou None se o arquivo não existe."""
    if not FLUXO.is_file():
        return None
    return json.loads(FLUXO.read_text(encoding="utf-8"))


def _erros_do_fluxo() -> list[str]:
    """Roda o MESMO validador de `flow.schema.validar`, o único que existe.

    Um só lugar decide o que é um roteiro publicável — é isso que sustenta a promessa
    "o fluxo que você desenha é o fluxo que roda". O import é tardio porque `--check` de
    quem só edita persona não precisa carregar o motor.
    """
    if not FLUXO.is_file():
        return []
    try:
        grafo = carregar_fluxo()
    except json.JSONDecodeError as e:
        return [
            f"fluxo.json não é JSON válido: linha {e.lineno}, coluna {e.colno} — {e.msg}"
        ]

    from app.flow import schema  # noqa: PLC0415
    from app.services import salao_fluxo  # noqa: PLC0415, F401 — registra agenda e acao

    return [f"fluxo.json — {e}" for e in schema.validar(grafo)]


async def sincronizar(nome_tenant: str, dry_run: bool, forcar: bool = False) -> int:
    from sqlalchemy import text  # noqa: PLC0415 — só quando há banco a tocar

    from app.core.database import AsyncSessionLocal  # noqa: PLC0415
    from app.services import kb  # noqa: PLC0415
    from app.services.bot_settings import (  # noqa: PLC0415
        load_bot_config,
        resolve_embeddings,
    )

    from app.services import flow_store  # noqa: PLC0415

    persona = PERSONA.read_text(encoding="utf-8").strip()
    conhecimento = CONHECIMENTO.read_text(encoding="utf-8").strip()
    docs = documentos()
    fluxo = carregar_fluxo()

    async with AsyncSessionLocal() as db:
        linha = (
            await db.execute(
                text("SELECT id FROM tenants WHERE lower(name) = lower(:n) LIMIT 1"),
                {"n": nome_tenant},
            )
        ).fetchone()
        if linha is None:
            print(f"ERRO: não existe conta chamada {nome_tenant!r}.", file=sys.stderr)
            print(
                "      Crie com: python3 scripts/criar_conta.py --nome ... --email ...",
                file=sys.stderr,
            )
            return 1
        tenant_id = linha.id

        # `load_bot_config` e não SELECT cru: a `api_key` é Fernet no banco, e lê-la
        # direto entregaria o ciphertext ao provedor de embeddings.
        cfg = await load_bot_config(db, tenant_id)

        print(f"\n── Conteúdo → {nome_tenant} ──────────────────")
        print(
            f"  persona.md       {len(persona):>5} car. "
            f"{'(sem mudança)' if cfg.persona.strip() == persona else '(ATUALIZA)'}"
        )
        print(
            f"  conhecimento.md  {len(conhecimento):>5} car. "
            f"{'(sem mudança)' if cfg.knowledge.strip() == conhecimento else '(ATUALIZA)'}"
        )

        existentes = {
            r.source
            for r in (
                await db.execute(
                    text("SELECT source FROM kb_sources WHERE tenant_id = :t"),
                    {"t": str(tenant_id)},
                )
            ).fetchall()
        }
        for source, title, corpo in docs:
            marca = "ATUALIZA" if source in existentes else "NOVO"
            print(f"  base/{source}.md  {len(corpo):>5} car.  ({marca}) — {title}")
        orfas = existentes - {s for s, _, _ in docs}
        for s in sorted(orfas):
            print(f"  base/{s}  REMOVE (não há mais arquivo)")
        atual_fluxo = (
            await flow_store.carregar(db, tenant_id) if fluxo is not None else None
        )
        painel_assumiu = bool(atual_fluxo and not atual_fluxo["gerenciado_por_arquivo"])
        if fluxo is not None:
            versao = (atual_fluxo or {}).get("versao", 0)
            destino = (
                "IGNORA — o roteiro foi assumido no painel"
                if painel_assumiu and not forcar
                else f"publica a versão {versao + 1}"
            )
            print(
                f"  fluxo.json       {len(fluxo.get('nos') or {}):>5} caixas ({destino})"
            )
        print("──────────────────────────────────────────────")

        if dry_run:
            pendentes = placeholders_pendentes()
            if pendentes:
                print("\nAinda há placeholder a preencher (bloqueia a escrita real):")
                for p in pendentes:
                    print(f"  - {p}")
            print("--dry-run: nada foi escrito.")
            return 0

        # Só aqui, e não em `validar`: um `[NOME DO SALÃO]` literal chegando ao cliente é
        # pior que agenda vazia, mas o molde do repositório tem placeholder por definição.
        pendentes = placeholders_pendentes()
        if pendentes:
            print(
                "ERRO: há placeholder não preenchido — isso chegaria ao cliente "
                "literalmente:",
                file=sys.stderr,
            )
            for p in pendentes:
                print(f"  - {p}", file=sys.stderr)
            return 1

        # Embeddings custam dinheiro e exigem chave. Esta checagem é a única que existe —
        # a rota que também barrava isso saiu quando a escrita passou a ser só por
        # arquivo. Sem chave a ingestão falharia em silêncio e a base ficaria com os
        # chunks antigos, divergindo do arquivo sem ninguém notar.
        alvo = resolve_embeddings(cfg)
        if docs and alvo is None:
            print(
                "ERRO: há documentos em conteudo/base/ e nenhuma chave de embeddings.",
                file=sys.stderr,
            )
            print(
                "      Configure EMBEDDINGS_API_KEY no .env, ou escolha um provedor que "
                "embeda (Gemini/OpenAI) com chave em Configurações › Agente de IA.",
                file=sys.stderr,
            )
            return 1

        await db.execute(
            text(
                "INSERT INTO bot_settings (tenant_id, persona, knowledge, "
                "conteudo_gerenciado) VALUES (:t, :p, :k, true) "
                "ON CONFLICT (tenant_id) DO UPDATE SET persona = EXCLUDED.persona, "
                "knowledge = EXCLUDED.knowledge, conteudo_gerenciado = true, "
                "updated_at = now()"
            ),
            {"t": str(tenant_id), "p": persona, "k": conhecimento},
        )

        # Roteiro assumido no painel não é sobrescrito em silêncio. Alguém escreveu
        # aquele texto por lá, e vê-lo sumir porque outra pessoa rodou o sincronizador é a
        # mesma perda que `conteudo_gerenciado` evita no sentido contrário, com a persona.
        if fluxo is not None and painel_assumiu and not forcar:
            print(
                "  fluxo.json: NÃO publicado — este roteiro foi assumido no painel.\n"
                "              Use --forcar para descartar a edição de lá e voltar ao arquivo."
            )
        elif fluxo is not None:
            versao = await flow_store.salvar(
                db,
                tenant_id,
                fluxo,
                fluxo.get("nome", ""),
                gerenciado_por_arquivo=True,
            )
            print(f"  fluxo.json: publicado na versão {versao}")

        # Commit ANTES do RAG, de propósito. O roteiro e a persona não têm nada a ver com
        # embeddings, e o cliente que roda em modo fluxo costuma não ter chave nenhuma —
        # deixar uma falha de indexação desfazer a publicação do roteiro tornaria o
        # atendimento sem IA refém de uma dependência de IA. Falha no RAG abaixo desfaz só
        # o RAG, e a mensagem de erro diz que nada foi apagado.
        await db.commit()

        for source, title, corpo in docs:
            trechos = await kb.ingest_document(
                db, tenant_id, source, title, corpo, alvo.api_key, alvo.provider
            )
            if trechos == 0:
                print(
                    f"ERRO: falha ao indexar base/{source}.md (embeddings). "
                    "Nada foi apagado; rode de novo depois de conferir a chave.",
                    file=sys.stderr,
                )
                await db.rollback()
                return 1
            print(f"  base/{source}: {trechos} trecho(s)")

        for s in sorted(orfas):
            await kb.delete_source(db, tenant_id, s)
        await db.commit()

    print(
        "\nSincronizado. A tela /agente passa a mostrar persona e conhecimento em "
        "leitura, apontando para os arquivos."
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Sincroniza conteudo/ para o banco de uma conta.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--conta", help="nome da empresa (tenant) que recebe o conteúdo")
    p.add_argument("--dry-run", action="store_true", help="mostra o diff e não escreve")
    p.add_argument(
        "--check", action="store_true", help="só valida os arquivos, sem tocar no banco"
    )
    p.add_argument(
        "--forcar",
        action="store_true",
        help="publica o fluxo por cima de um roteiro assumido no painel (descarta a edição de lá)",
    )
    args = p.parse_args()

    erros = validar()
    if erros:
        print("Conteúdo reprovado:\n", file=sys.stderr)
        for e in erros:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if args.check:
        print("Conteúdo OK.")
        return 0
    if not args.conta:
        print("ERRO: informe --conta (ou use --check).", file=sys.stderr)
        return 1
    return asyncio.run(sincronizar(args.conta.strip(), args.dry_run, args.forcar))


if __name__ == "__main__":
    raise SystemExit(main())
