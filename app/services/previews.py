"""Preview de uma mensagem na lista de conversas.

Vive fora de `ingestion.py` porque agora tem três autores: a mensagem que CHEGA do
cliente, a que o atendente ENVIA com anexo e a transcrição do áudio. Tabelas de rótulo
divergindo é o tipo de coisa que ninguém percebe até a lista mostrar "Imagem" de um lado
e "Arquivo" do outro para o mesmo tipo de conteúdo.

Sem emoji de propósito: a interface marca tipo de mídia com ícone, e o preview é texto
puro dentro de uma coluna estreita.
"""

# Preview de mídia sem texto (áudio antes da transcrição, imagem sem legenda...).
_MEDIA_PREVIEW = {
    "IMAGE": "Imagem",
    "VIDEO": "Vídeo",
    "AUDIO": "Áudio",
    "DOCUMENT": "Documento",
    "STICKER": "Figurinha",
}

# O preview é uma coluna de texto curta na listagem; cortar aqui evita empurrar
# mensagem longa inteira para dentro de `conversations`.
PREVIEW_MAX = 120


def preview_for(content_type_name: str, content: str | None) -> str:
    """Texto da mensagem quando há, senão o rótulo do tipo de mídia.

    `content_type_name` é o NOME do enum (maiúsculo), que é como o Postgres guarda.
    """
    texto = (content or "").strip()
    if texto:
        return texto[:PREVIEW_MAX]
    return _MEDIA_PREVIEW.get(content_type_name, "")


def preview_transcricao(transcript: str) -> str:
    """Preview do áudio já transcrito.

    Prefixo dito e não sugerido por emoji: sem ele a linha fica indistinguível de uma
    mensagem que o cliente digitou.
    """
    return ("Áudio: " + transcript)[:PREVIEW_MAX]
