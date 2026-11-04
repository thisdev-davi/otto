from pydantic import model_validator
from pydantic_settings import BaseSettings

# Valores que já vazaram para `.env.example`, `docker-compose.yml` e para todo clone do
# repositório. Fora de `dev` eles não são segredo nenhum — são senha publicada.
_SEGREDOS_PROIBIDOS = frozenset(
    {
        "changeme",
        "change-this-secret-key-in-production",
        "secret",
        "password",
        "",
    }
)

_TAMANHO_MINIMO_SEGREDO = 32


class Settings(BaseSettings):
    DATABASE_URL: str
    DATABASE_DIRECT_URL: str = ""
    # SQL parameters may contain personal data; opt in only for local debugging.
    SQL_ECHO: bool = False
    REDIS_URL: str
    EVOLUTION_URL: str
    EVOLUTION_API_KEY: str
    # Base pública da API p/ Evolution entregar webhooks (ex.: http://host.docker.internal:8000).
    # Vazio = não configura webhook no provision (parear ainda funciona).
    WEBHOOK_BASE_URL: str = ""
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    # 7 dias, não 30: os dois tokens ficam no localStorage do navegador, então qualquer
    # XSS vira sequestro de sessão pelo tempo que restar do refresh. Uma semana mantém o
    # atendente logado entre plantões sem transformar um vazamento em acesso mensal.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # Anti-flood: o bot NÃO auto-responde mensagens mais antigas que isto (segundos).
    # Protege contra o backlog que o WhatsApp entrega ao (re)conectar. 0 = desliga a guarda.
    BOT_REPLY_MAX_AGE_SECONDS: int = 300
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    ENV: str = "dev"
    # Há um reverse proxy (nginx) na frente? Só com isto ligado o `X-Forwarded-For` é
    # respeitado no rate limit. Ligar sem proxy real deixa qualquer um forjar o próprio
    # IP e escapar dos limites; deixar desligado ATRÁS de proxy faz todo mundo compartilhar
    # o mesmo balde (127.0.0.1) e um cliente barulhento derrubar os outros.
    TRUSTED_PROXY: bool = False
    # Rate limits. Login é por (IP) e por (e-mail) — os dois contam separado.
    RATE_LIMIT_LOGIN: int = 5
    RATE_LIMIT_LOGIN_JANELA: int = 60
    RATE_LIMIT_WEBHOOK: int = 300
    RATE_LIMIT_WEBHOOK_JANELA: int = 60
    # Teto diário de turnos do agente por tenant. Os limites de `bot_orchestrator` são por
    # CONVERSA, e abrir conversa é grátis para quem manda a mensagem — sem um teto por
    # tenant não existe limite nenhum de gasto com LLM. 0 = sem teto.
    AI_MAX_TURNS_DIA: int = 500
    # Silêncio, em segundos, depois do qual a conversa é dada como encerrada e a próxima
    # mensagem do cliente abre uma conversa NOVA — com o roteiro começando do começo.
    #
    # Existe porque conversa parada guarda um estado que envelhece mal: quem some no meio
    # de "para qual data?" e volta três horas depois dizendo "oi" recebia "não entendi",
    # e na segunda tentativa ia para a fila. O cliente não errou nada — ele só não sabia
    # que a conversa continuava aberta numa pergunta.
    #
    # Não há `celery beat` no projeto e não se cria um para isto: o encerramento é
    # PREGUIÇOSO, avaliado quando a próxima mensagem chega (mesma escolha de
    # `salao_agenda.expirar_vencidas`). 0 = desliga.
    CONVERSA_INATIVA_SEGUNDOS: int = 3600
    # Agente de IA — fallback de ambiente quando o tenant não configura chave própria.
    # "stub" = provider determinístico sem rede (testes/CI).
    LLM_PROVIDER: str = "stub"
    LLM_MODEL: str = ""
    LLM_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    # Embeddings do RAG vetorial. Vazio = herda a chave do LLM quando o provider dele
    # também sabe embedar (openai/gemini) — uma chave Gemini cobre chat e embeddings.
    EMBEDDINGS_PROVIDER: str = "openai"  # openai | gemini
    EMBEDDINGS_API_KEY: str = ""
    # Mídia inbound + transcrição de áudio.
    MEDIA_DIR: str = "data/media"
    MEDIA_STORAGE_BACKEND: str = "local"  # local | s3
    S3_BUCKET: str = ""
    S3_ENDPOINT_URL: str = ""
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_ADDRESSING_STYLE: str = "auto"
    # Estes três são o FALLBACK. A transcrição usa primeiro a chave do painel do tenant
    # (`bot_settings.resolve_transcription`), para que quem configurou o Gemini não
    # precise de uma segunda conta de IA só para o áudio.
    TRANSCRIPTION_PROVIDER: str = "groq"  # gemini | groq | openai
    TRANSCRIPTION_MODEL: str = ""  # vazio = default do provider
    TRANSCRIPTION_API_KEY: str = ""

    # extra="ignore": env vars não declaradas (ex.: canais removidos) não quebram o boot.
    model_config = {"env_file": ".env", "extra": "ignore"}

    @model_validator(mode="after")
    def _recusar_segredo_de_exemplo(self) -> "Settings":
        """Fora de `dev`, aborta o boot se um segredo ainda for o placeholder do repo.

        Falhar aqui é barulhento e imediato. A alternativa é o que existia antes: subir
        em produção com `EVOLUTION_API_KEY=changeme`, valor que está publicado em
        `.env.example` e no `docker-compose.yml` — e esse segredo é o ÚNICO portão do
        `/webhooks`, de onde se injeta mensagem em qualquer tenant. Um boot quebrado no
        deploy custa minutos; esse segredo em produção custa a base inteira.
        """
        if self.MEDIA_STORAGE_BACKEND not in {"local", "s3"}:
            raise ValueError("MEDIA_STORAGE_BACKEND must be local or s3")
        if self.MEDIA_STORAGE_BACKEND == "s3" and not self.S3_BUCKET:
            raise ValueError("S3_BUCKET is required for object storage")
        if self.S3_ADDRESSING_STYLE not in {"auto", "path", "virtual"}:
            raise ValueError("Invalid S3_ADDRESSING_STYLE")
        if self.ENV.strip().lower() == "dev":
            return self

        for nome in ("SECRET_KEY", "EVOLUTION_API_KEY"):
            valor = (getattr(self, nome) or "").strip()
            if valor.lower() in _SEGREDOS_PROIBIDOS:
                raise ValueError(
                    f"{nome} está com um valor de exemplo ({valor!r}) e ENV={self.ENV}. "
                    f"Gere um segredo real: python -c "
                    f'"import secrets; print(secrets.token_urlsafe(48))"'
                )
            if len(valor) < _TAMANHO_MINIMO_SEGREDO:
                raise ValueError(
                    f"{nome} tem {len(valor)} caracteres; o mínimo fora de dev é "
                    f"{_TAMANHO_MINIMO_SEGREDO}."
                )
        return self


settings = Settings()
