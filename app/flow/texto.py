"""Interpolação de `{{variavel}}` no texto do roteiro.

**Substituição literal, NUNCA `str.format`.** Mesma razão que fez
`bot_settings.render_confirmacao` existir: sobre texto que alguém edita, `str.format`
levanta `KeyError` numa chave que não conhecemos e, pior, `{x.__class__.__init__.__globals__}`
alcança objetos do processo.

**Variável sem valor não vira string vazia.** "O valor dessa data é ." é pior que erro:
sai como mensagem para um cliente e ninguém fica sabendo. `renderizar` devolve o que
faltou, e o motor trata falta de variável como roteiro quebrado — encaminha para a equipe
com nota interna, em vez de mandar uma frase mutilada.
"""

import re

_VARIAVEL = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def renderizar(texto: str, valores: dict) -> tuple[str, list[str]]:
    """`(texto preenchido, nomes que faltaram)`."""
    faltando: list[str] = []

    def troca(m: re.Match) -> str:
        nome = m.group(1)
        valor = valores.get(nome)
        if valor is None or str(valor).strip() == "":
            faltando.append(nome)
            return ""
        return str(valor)

    return _VARIAVEL.sub(troca, texto or ""), faltando
