"""O que o bot diz sobre mídia que ele não lê — um texto só, para os dois cérebros.

Mora aqui, e não dentro de um dos dois, porque a frase que chega ao cliente não pode
depender de qual cérebro está ligado: trocar "Roteiro" por "Agente" no painel não pode
mudar o que se diz sobre uma foto. Duas cópias divergiriam na primeira vez que alguém
reescrevesse uma delas.

Nenhuma frase presume menu. Elas são seguidas do texto do nó atual no modo fluxo, e
aparecem também antes da saudação quando a conversa COMEÇA por uma foto.
"""

# "Recebi seu arquivo" para uma foto soa como se o bot tivesse olhado e não entendido.
# Ele não olhou — dizer isso claro é o que faz o cliente reescrever em vez de reenviar.
_TEXTO = {
    "IMAGE": "Recebi sua foto! Só que por aqui eu não consigo ver imagens 😅",
    "VIDEO": "Recebi seu vídeo! Só que por aqui eu não consigo assistir 😅",
    "AUDIO": "Recebi seu áudio, mas não consegui entender o que você falou 😅",
    "STICKER": "Recebi sua figurinha 😄",
    "DOCUMENT": "Recebi seu envio! Só que por aqui eu não consigo abrir arquivos 😅",
}
_PADRAO = _TEXTO["DOCUMENT"]

# Como a mídia é descrita na nota interna que a equipe lê antes de assumir.
_ROTULO = {
    "IMAGE": "uma foto",
    "VIDEO": "um vídeo",
    "AUDIO": "um áudio que não deu para transcrever",
    "STICKER": "uma figurinha",
    "DOCUMENT": "um arquivo",
}


def texto(content_type: str) -> str:
    """A frase para o cliente. Tipo desconhecido cai no genérico, nunca em vazio."""
    return _TEXTO.get(content_type, _PADRAO)


def rotulo(content_type: str) -> str:
    """Como a equipe lê o tipo na nota interna."""
    return _ROTULO.get(content_type, "um arquivo")
