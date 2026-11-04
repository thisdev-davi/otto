"""O formato do grafo e o validador que impede um roteiro quebrado de ir ao ar.

**Puro de propósito**: nada aqui toca banco nem rede, então o mesmo `validar()` roda no
teste, no `scripts/sincronizar_conteudo.py --check` e antes de todo `PUT /fluxo`. É o que
sustenta a promessa "o fluxo que você desenha é o fluxo que roda".

Formato:

    {
      "nome": "Atendimento padrão",
      "inicio": "horario",
      "config": {"max_invalidas": 2, "no_handoff": "chamar_equipe",
                 "palavras_atendente": ["atendente", "humano"],
                 "nao_entendi": "Não entendi 😅"},
      "nos": {
        "saudacao": {"tipo": "mensagem", "pos": [40, 152],
                     "texto": "Oi! 👋|||Posso ajudar?", "proximo": "menu_principal"},
        ...
      }
    }

Saídas ficam sempre num destes lugares, e `saidas_de()` é o ÚNICO ponto que sabe disso:
`proximo`, `saidas.<nome>`, `opcoes[].proximo`, `testes[].proximo`, `senao`.
"""

import copy
import re

from app.flow import registro
from app.flow.registro import Espec

# Separador de bolhas, o mesmo de `llm.split_bubbles` — é ele que o orquestrador quebra.
SEPARADOR_BOLHA = "|||"

MAX_INVALIDAS_PADRAO = 2
# Teto de nós percorridos num turno. Rede contra um ciclo que o validador não pegou; o
# roteiro real mais longo tem 6 nós entre duas perguntas, então 20 é folga larga.
MAX_NOS_POR_TURNO = 20

PALAVRAS_ATENDENTE_PADRAO = (
    "atendente",
    "humano",
    "pessoa de verdade",
    "falar com alguem",
    "falar com alguém",
)

VALIDACOES = ("texto", "data", "numero")

# Os SETE tipos da plataforma. `agenda` e `acao` são do nicho e entram por
# `registro.registrar()` — ver `app/services/salao_fluxo.py`.
ESPEC: dict[str, Espec] = {
    "mensagem": Espec(saidas=("proximo",), tem_texto=True),
    "menu": Espec(saidas=(), tem_texto=True, espera_resposta=True),
    "pergunta": Espec(
        saidas=("proximo",), campos=("campo",), tem_texto=True, espera_resposta=True
    ),
    "condicao": Espec(saidas=("senao",), campos=("campo",)),
    "horario": Espec(saidas=("dentro", "fora")),
    "handoff": Espec(saidas=(), terminal=True, tem_texto=True),
    "fim": Espec(saidas=(), terminal=True, tem_texto=True),
}

# Preço tem uma fonte só: a agenda, em tempo real (`CLAUDE.md` regra 4). Escrito no
# roteiro, vira um segundo preço competindo com ela — e é o texto estático que estará
# errado no dia em que o dono vender uma quinta-feira. Mesmo padrão do sincronizador de
# conteúdo, pelo mesmo motivo.
PADRAO_DINHEIRO = re.compile(r"R\$|\breais\b", re.IGNORECASE)

_VARIAVEL = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def espec_de(tipo: str) -> Espec | None:
    return ESPEC.get(tipo) or registro.espec_de(tipo)


def tipos_conhecidos() -> tuple[str, ...]:
    return tuple(sorted(set(ESPEC) | set(registro.registrados())))


def saidas_de(no: dict) -> list[tuple[str, str]]:
    """`(nome da saída, id do destino)` de um nó. Único lugar que conhece o formato."""
    fora: list[tuple[str, str]] = []
    if isinstance(no.get("proximo"), str) and no["proximo"]:
        fora.append(("proximo", no["proximo"]))
    if isinstance(no.get("senao"), str) and no["senao"]:
        fora.append(("senao", no["senao"]))
    saidas = no.get("saidas")
    if isinstance(saidas, dict):
        for nome, destino in saidas.items():
            if isinstance(destino, str) and destino:
                fora.append((str(nome), destino))
    for i, op in enumerate(no.get("opcoes") or []):
        if isinstance(op, dict) and isinstance(op.get("proximo"), str):
            fora.append((f"opcao[{i}]", op["proximo"]))
    for i, t in enumerate(no.get("testes") or []):
        if isinstance(t, dict) and isinstance(t.get("proximo"), str):
            fora.append((f"teste[{i}]", t["proximo"]))
    return fora


def textos_de(no: dict) -> list[str]:
    """Todo texto que pode chegar ao cliente a partir deste nó."""
    fora = []
    if isinstance(no.get("texto"), str):
        fora.append(no["texto"])
    for op in no.get("opcoes") or []:
        if isinstance(op, dict) and isinstance(op.get("rotulo"), str):
            fora.append(op["rotulo"])
    return fora


def variaveis_de(texto: str) -> set[str]:
    return set(_VARIAVEL.findall(texto or ""))


# ─────────────────────────── edição pelo painel ───────────────────────────
#
# O painel edita TEXTO, nunca ESTRUTURA. Não é preguiça de fazer um editor completo: é a
# trava que faz a edição pelo cliente ser segura. Quem não consegue mexer em `proximo`,
# `saidas`, `tipo` ou no número de opções não consegue criar aresta solta, apagar um ramo
# de `MOTIVO_*` nem inventar um ciclo — a classe inteira de defeito some por construção,
# em vez de depender do validador pegar depois.
#
# `sinonimos` entra porque é o que faz "quero ver as fotos" cair na opção 2, e quem
# descobre a palavra que falta é quem lê as conversas, não quem escreveu o roteiro.
CAMPOS_TEXTO_DO_NO = ("texto", "resumo", "sem_livres")
CAMPOS_TEXTO_DA_OPCAO = ("rotulo", "sinonimos")
CAMPOS_TEXTO_DA_CONFIG = ("nao_entendi",)


def aplicar_textos(grafo: dict, edicoes: dict) -> tuple[dict, list[str]]:
    """Aplica só texto sobre o grafo PUBLICADO. `(novo grafo, erros)`.

    Recebe o que mudou, nunca um grafo inteiro vindo do navegador: assim o cliente não
    tem como alterar a estrutura mesmo que tente, e o servidor não precisa confiar no que
    chegou. Chave desconhecida vira erro em voz alta, não campo ignorado em silêncio.
    """
    novo = copy.deepcopy(grafo)
    erros: list[str] = []
    nos = novo.get("nos") or {}

    for nome in edicoes.get("config") or {}:
        if nome not in CAMPOS_TEXTO_DA_CONFIG:
            erros.append(f"config.{nome} não é um texto editável pelo painel")
    if not erros and edicoes.get("config"):
        novo.setdefault("config", {}).update(edicoes["config"])

    for nid, mudanca in (edicoes.get("nos") or {}).items():
        no = nos.get(nid)
        if no is None:
            erros.append(f"{nid}: esse nó não existe no roteiro publicado")
            continue
        for campo, valor in (mudanca or {}).items():
            if campo == "opcoes":
                erros += _aplicar_opcoes(nid, no, valor)
            elif campo in CAMPOS_TEXTO_DO_NO:
                no[campo] = str(valor)
            else:
                erros.append(
                    f"{nid}: `{campo}` não é texto — a estrutura do roteiro só muda por "
                    "conteudo/fluxo.json"
                )

    if erros:
        return grafo, erros
    return novo, validar(novo)


def _aplicar_opcoes(nid: str, no: dict, valor) -> list[str]:
    """Rótulos e sinônimos, na MESMA quantidade e na MESMA ordem das opções publicadas."""
    atuais = no.get("opcoes") or []
    if not isinstance(valor, list) or len(valor) != len(atuais):
        return [
            f"{nid}: são {len(atuais)} opções no roteiro publicado — o painel muda o "
            "texto delas, não quantas são"
        ]
    for i, mudanca in enumerate(valor):
        if not isinstance(mudanca, dict):
            return [f"{nid}: opção {i + 1} veio num formato inesperado"]
        for campo, novo_valor in mudanca.items():
            if campo not in CAMPOS_TEXTO_DA_OPCAO:
                return [f"{nid}: opção {i + 1} — `{campo}` não é texto editável"]
            if campo == "sinonimos":
                atuais[i]["sinonimos"] = [
                    str(x) for x in (novo_valor or []) if str(x).strip()
                ]
            else:
                atuais[i][campo] = str(novo_valor)
    return []


def validar(grafo: dict) -> list[str]:
    """Lista de problemas. Vazia = o grafo pode ir ao ar."""
    erros: list[str] = []
    if not isinstance(grafo, dict):
        return ["o fluxo precisa ser um objeto JSON"]

    nos = grafo.get("nos")
    if not isinstance(nos, dict) or not nos:
        return ["o fluxo não tem nós (`nos`)"]

    inicio = grafo.get("inicio")
    if not isinstance(inicio, str) or inicio not in nos:
        erros.append(f"`inicio` aponta para um nó que não existe: {inicio!r}")

    config = grafo.get("config") or {}
    if not isinstance(config, dict):
        erros.append("`config` precisa ser um objeto")
        config = {}

    erros += _erros_por_no(nos)
    erros += _erros_de_config(config, nos)
    if isinstance(inicio, str) and inicio in nos:
        erros += _erros_de_alcance(nos, inicio, config)
        erros += _erros_de_ciclo(nos)
    return erros


def _erros_por_no(nos: dict) -> list[str]:
    erros: list[str] = []
    for nid, no in nos.items():
        if not isinstance(no, dict):
            erros.append(f"{nid}: precisa ser um objeto")
            continue
        tipo = no.get("tipo")
        espec = espec_de(tipo) if isinstance(tipo, str) else None
        if espec is None:
            erros.append(
                f"{nid}: tipo desconhecido {tipo!r} "
                f"(conhecidos: {', '.join(tipos_conhecidos())})"
            )
            continue

        for campo in espec.campos:
            if not no.get(campo):
                erros.append(f"{nid}: falta o campo obrigatório `{campo}`")

        # `.strip()` e não só truthiness: `"  "` é verdadeiro em Python, e um texto de
        # espaços passa pelo validador para virar uma bolha que o motor descarta na hora
        # de enviar — o nó fica mudo sem ninguém saber por quê.
        if (
            espec.tem_texto
            and tipo not in ("handoff", "fim")
            and not str(no.get("texto") or "").strip()
        ):
            erros.append(
                f"{nid}: `texto` vazio — o cliente receberia uma bolha em branco"
            )

        if tipo == "pergunta":
            val = no.get("validacao", "texto")
            if val not in VALIDACOES:
                erros.append(
                    f"{nid}: `validacao` {val!r} não existe "
                    f"(use {', '.join(VALIDACOES)})"
                )
        if tipo == "menu":
            erros += _erros_de_menu(nid, no)
        if tipo == "condicao" and not isinstance(no.get("testes"), list):
            erros.append(f"{nid}: `testes` precisa ser uma lista")

        for nome in espec.saidas:
            destino = (no.get("saidas") or {}).get(nome) or (
                no.get(nome) if nome in ("proximo", "senao") else None
            )
            if not destino:
                erros.append(f"{nid}: falta a saída `{nome}`")

        for nome, destino in saidas_de(no):
            if destino not in nos:
                erros.append(
                    f"{nid}: saída `{nome}` aponta para {destino!r}, que não existe"
                )

        if not espec.terminal and not saidas_de(no):
            erros.append(f"{nid}: não é terminal e não tem nenhuma saída")

        pos = no.get("pos")
        if pos is not None and (
            not isinstance(pos, (list, tuple))
            or len(pos) != 2
            or not all(isinstance(v, (int, float)) for v in pos)
        ):
            erros.append(f"{nid}: `pos` precisa ser [x, y] em números")

        for texto in textos_de(no):
            if PADRAO_DINHEIRO.search(texto):
                erros.append(
                    f"{nid}: o texto fala em dinheiro. Preço vem da agenda em tempo real "
                    "— use a variável que o nó `agenda` preenche, nunca um valor escrito."
                )
                break
    return erros


def _erros_de_menu(nid: str, no: dict) -> list[str]:
    opcoes = no.get("opcoes")
    if not isinstance(opcoes, list) or not opcoes:
        return [f"{nid}: menu sem `opcoes`"]
    erros = []
    for i, op in enumerate(opcoes):
        if not isinstance(op, dict):
            erros.append(f"{nid}: opção {i + 1} precisa ser um objeto")
            continue
        if not op.get("rotulo"):
            erros.append(f"{nid}: opção {i + 1} sem `rotulo`")
        if not op.get("proximo"):
            erros.append(f"{nid}: opção {i + 1} sem `proximo`")
    return erros


def _erros_de_config(config: dict, nos: dict) -> list[str]:
    erros = []
    saida = config.get("no_handoff")
    if not saida:
        erros.append(
            "`config.no_handoff` é obrigatório: é para onde vai quem sai do roteiro"
        )
    elif saida not in nos:
        erros.append(f"`config.no_handoff` aponta para {saida!r}, que não existe")
    elif nos[saida].get("tipo") != "handoff":
        erros.append(
            f"`config.no_handoff` ({saida}) precisa ser um nó do tipo `handoff`"
        )
    return erros


def _erros_de_alcance(nos: dict, inicio: str, config: dict) -> list[str]:
    """Nó inalcançável é quase sempre uma ligação esquecida, não um nó a mais."""
    vistos = {inicio}
    fila = [inicio]
    saida = config.get("no_handoff")
    if saida in nos:
        vistos.add(saida)
        fila.append(saida)
    while fila:
        atual = fila.pop()
        for _, destino in saidas_de(nos.get(atual) or {}):
            if destino in nos and destino not in vistos:
                vistos.add(destino)
                fila.append(destino)
    orfaos = sorted(set(nos) - vistos)
    return [f"{nid}: nenhum caminho leva até aqui" for nid in orfaos]


def _erros_de_ciclo(nos: dict) -> list[str]:
    """Ciclo que não passa por nó de espera trava o motor até o teto do turno.

    O motor caminha até encontrar um nó que espera resposta (menu, pergunta) ou um
    terminal. Um ciclo formado só por nós que NÃO esperam é uma volta infinita dentro de
    um único turno — o teto de `MAX_NOS_POR_TURNO` salva o cliente, mas o roteiro está
    errado e isso tem de doer na publicação, não em produção.
    """

    def anda(nid: str) -> bool:
        no = nos.get(nid) or {}
        espec = espec_de(no.get("tipo"))
        return espec is not None and not espec.espera_resposta and not espec.terminal

    cor: dict[str, int] = {}
    achados: list[str] = []

    def visita(nid: str, caminho: list[str]) -> None:
        cor[nid] = 1
        for _, destino in saidas_de(nos.get(nid) or {}):
            if destino not in nos or not anda(destino):
                continue
            if cor.get(destino) == 1:
                volta = (
                    caminho[caminho.index(destino) :]
                    if destino in caminho
                    else [destino]
                )
                achados.append(
                    "ciclo sem nó de espera: "
                    + " → ".join(volta + [destino])
                    + " — o motor giraria até o teto do turno"
                )
            elif cor.get(destino, 0) == 0:
                visita(destino, caminho + [destino])
        cor[nid] = 2

    for nid in nos:
        if anda(nid) and cor.get(nid, 0) == 0:
            visita(nid, [nid])
    return achados
