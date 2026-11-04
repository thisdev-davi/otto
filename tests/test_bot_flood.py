"""Guarda anti-flood: o bot não auto-responde mensagens antigas (backlog ao conectar)."""

from datetime import datetime, timedelta, timezone

from app.services import bot_orchestrator
from app.services.bot_orchestrator import _is_stale


class TestIsStale:
    def test_mensagem_recente_nao_e_stale(self):
        now = datetime.now(timezone.utc)
        assert _is_stale(now) is False
        assert _is_stale(now - timedelta(seconds=10)) is False

    def test_mensagem_antiga_e_stale(self):
        old = datetime.now(timezone.utc) - timedelta(days=2)
        assert _is_stale(old) is True

    def test_limite_exato(self, monkeypatch):
        monkeypatch.setattr(bot_orchestrator.settings, "BOT_REPLY_MAX_AGE_SECONDS", 300)
        # 5min + 1s atrás → stale; 4min atrás → ok
        assert _is_stale(datetime.now(timezone.utc) - timedelta(seconds=301)) is True
        assert _is_stale(datetime.now(timezone.utc) - timedelta(seconds=240)) is False

    def test_none_nao_e_stale(self):
        assert _is_stale(None) is False

    def test_guarda_desligada_com_zero(self, monkeypatch):
        monkeypatch.setattr(bot_orchestrator.settings, "BOT_REPLY_MAX_AGE_SECONDS", 0)
        old = datetime.now(timezone.utc) - timedelta(days=30)
        assert _is_stale(old) is False

    def test_naive_datetime_tratado_como_utc(self, monkeypatch):
        monkeypatch.setattr(bot_orchestrator.settings, "BOT_REPLY_MAX_AGE_SECONDS", 300)
        naive_old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        assert _is_stale(naive_old) is True


class TestTypingDelay:
    """Cadência de envio. As bolhas saíam em rajada — o padrão que mais denuncia robô
    numa conexão baileys e que arrisca ban do número."""

    def test_proporcional_ao_tamanho(self):
        curta = bot_orchestrator._typing_delay("ok", index=1)
        longa = bot_orchestrator._typing_delay("x" * 200, index=1)
        assert longa > curta

    def test_piso_e_teto(self):
        assert (
            bot_orchestrator._typing_delay("a", index=1)
            == bot_orchestrator.TYPING_MIN_MS
        )
        assert (
            bot_orchestrator._typing_delay("x" * 5000, index=1)
            == bot_orchestrator.TYPING_MAX_MS
        )

    def test_primeira_bolha_responde_mais_rapido(self):
        texto = "x" * 200
        assert bot_orchestrator._typing_delay(
            texto, index=0
        ) < bot_orchestrator._typing_delay(texto, index=1)
        # 4s para devolver um "Oi" seria pior que responder rápido demais.
        assert (
            bot_orchestrator._typing_delay(texto, index=0)
            <= bot_orchestrator.TYPING_FIRST_MS
        )

    def test_envio_manual_do_atendente_nao_ganha_pausa(self):
        """O default de OutboundMessage tem que ser zero: quem precisa parecer humano é
        o bot, não o atendente que já é humano."""
        from uuid import uuid4

        from app.channels.types import OutboundMessage
        from app.models.enums import Channel, ContentType

        msg = OutboundMessage(
            channel=Channel.WHATSAPP,
            channel_connection_id=uuid4(),
            to_external_id="5511999",
            content_type=ContentType.TEXT,
            content="resposta do atendente",
        )
        assert msg.delay_ms == 0
