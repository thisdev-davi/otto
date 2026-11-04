"""URLs de mídia assinadas — substituem o JWT na query string.

**O problema que isto resolve.** `<img src>` e `<audio src>` não mandam header
`Authorization`: o navegador busca a URL sozinho. A solução anterior era pôr o access
token em `?token=`, o que funcionava mas espalhava uma credencial de sessão inteira por
todo lugar que registra URL — log de acesso do nginx, histórico do navegador, `Referer`.
Combinado com o XSS de mídia (ver `attachments.mimetype_para_servir`), virava roubo de
sessão: a página do atacante lia o próprio `location.search`.

**O que muda.** A assinatura vale para UM recurso, por um tempo curto, e não autoriza
mais nada na API. Vazou, o atacante vê aquele arquivo — não a conta.

A chave é derivada de `SECRET_KEY` com um rótulo próprio, e não `SECRET_KEY` cru: assim
uma assinatura daqui nunca pode ser confundida com um JWT, mesmo que algum código futuro
troque os dois de lugar.
"""

import base64
import hashlib
import hmac
import time
from uuid import UUID

from app.core.config import settings

# 6 horas. Curto o bastante para que uma URL vazada em log não valha muito depois, longo
# o bastante para a aba da inbox ficar aberta o dia de trabalho sem imagem quebrada —
# que foi o bug que fez o `?token=` existir em primeiro lugar.
TTL_PADRAO = 6 * 60 * 60

_ROTULO = b"otto.media-link.v1"


def _chave() -> bytes:
    return hmac.new(_ROTULO, settings.SECRET_KEY.encode(), hashlib.sha256).digest()


def _assinatura(recurso: str, exp: int) -> str:
    mac = hmac.new(_chave(), f"{recurso}:{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def recurso(tipo: str, item_id: UUID | str, tenant_id: UUID | str) -> str:
    """Identidade do que está sendo assinado.

    `tenant_id` entra na string assinada de propósito: sem ele, uma assinatura válida
    continuaria válida se a mesma mídia fosse lida no escopo de outro tenant.
    """
    return f"{tipo}:{item_id}:{tenant_id}"


def assinar(
    tipo: str,
    item_id: UUID | str,
    tenant_id: UUID | str,
    ttl: int = TTL_PADRAO,
    janela: int = 0,
) -> str:
    """Devolve o par `exp=...&sig=...` pronto para concatenar na URL.

    `janela` > 0 arredonda o vencimento PARA CIMA até o próximo múltiplo dela, e a URL
    passa a ser a mesma durante a janela inteira. Sem isso cada chamada gera um `exp`
    novo, a URL muda a cada segundo, e o `Cache-Control` da resposta nunca acerta: o
    navegador trata cada assinatura como um arquivo diferente. A validade nunca fica
    abaixo de `ttl` — o arredondamento só estica.
    """
    exp = int(time.time()) + ttl
    if janela > 0:
        exp = (exp // janela + 1) * janela
    sig = _assinatura(recurso(tipo, item_id, tenant_id), exp)
    return f"exp={exp}&sig={sig}"


def validar(
    tipo: str,
    item_id: UUID | str,
    tenant_id: UUID | str,
    exp: int | None,
    sig: str | None,
) -> bool:
    if not sig or exp is None:
        return False
    if exp < int(time.time()):
        return False
    esperada = _assinatura(recurso(tipo, item_id, tenant_id), exp)
    return hmac.compare_digest(esperada, sig)
