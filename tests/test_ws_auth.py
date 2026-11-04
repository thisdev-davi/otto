"""Testes de autenticação do WebSocket (decode_token) e precedência de status."""

import uuid

from app.api.auth import create_access_token, decode_token
from app.tasks.ingest import _STATUS_RANK


class TestDecodeToken:
    def test_token_valido(self):
        tid, uid = uuid.uuid4(), uuid.uuid4()
        token = create_access_token(tenant_id=tid, user_id=uid)
        data = decode_token(token)
        assert data is not None
        assert data.tenant_id == tid
        assert data.user_id == uid

    def test_token_invalido(self):
        assert decode_token("nao-e-um-jwt") is None

    def test_token_vazio(self):
        assert decode_token("") is None

    def test_token_assinado_com_outra_chave(self):
        from jose import jwt

        bogus = jwt.encode(
            {"tenant_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())},
            "chave-errada",
            algorithm="HS256",
        )
        assert decode_token(bogus) is None


class TestStatusPrecedence:
    def test_ordem_de_precedencia(self):
        # um ACK atrasado (DELIVERED) nunca deve sobrescrever READ
        assert _STATUS_RANK["SENT"] < _STATUS_RANK["DELIVERED"] < _STATUS_RANK["READ"]

    def test_failed_tem_rank_proprio(self):
        assert _STATUS_RANK["FAILED"] > 0


class TestWaitDisconnect:
    """`_wait_disconnect` é o mecanismo que faltava: alguém em `receive()`.

    Sem ele o handler ficava estacionado em `pubsub.listen()` e só perceberia a saída
    do cliente na próxima tentativa de `send_text` — que em tenant sem tráfego nunca
    vem. Cada aba fechada deixava um handler vivo segurando socket (CLOSE-WAIT) e uma
    conexão Redis; com o front reconectando a cada 3s, o servidor degradava sozinho.

    Por que testar a corrotina e não o endpoint inteiro: o `TestClient` do Starlette
    **cancela a task do app** quando o websocket fecha, então a limpeza acontece
    mesmo na versão vazando. Verifiquei — um teste de ponta a ponta aqui passa nos
    dois casos e daria falsa segurança. Reproduzir de verdade exigiria servidor real
    e cliente sumindo sem handshake; o que dá para fixar em teste barato é que a
    corrotina retorna no disconnect e ignora o resto.
    """

    def test_retorna_no_disconnect(self):
        import asyncio

        from app.api.ws import _wait_disconnect

        class _WS:
            def __init__(self, eventos):
                self._eventos = list(eventos)

            async def receive(self):
                if not self._eventos:
                    raise AssertionError("receive() além do disconnect")
                return self._eventos.pop(0)

        # Mensagens do cliente são ignoradas; só o disconnect encerra.
        ws = _WS(
            [
                {"type": "websocket.receive", "text": "ruido"},
                {"type": "websocket.receive", "text": "mais ruido"},
                {"type": "websocket.disconnect", "code": 1001},
            ]
        )
        asyncio.run(asyncio.wait_for(_wait_disconnect(ws), timeout=2))

    def test_endpoint_corre_pump_e_watch_em_paralelo(self):
        """Guard estrutural: se alguém remover o watcher da corrida, isto quebra."""
        import inspect

        from app.api import ws as ws_mod

        fonte = inspect.getsource(ws_mod.websocket_endpoint)
        assert "_wait_disconnect" in fonte
        assert "FIRST_COMPLETED" in fonte
