import uuid
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    DateTime,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
    Enum as SAEnum,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import func
from app.models.enums import (
    Channel,
    ConversationStatus,
    MessageDirection,
    MessageStatus,
    ContentType,
    ConversationEventAction,
    UserRole,
)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(
        SAEnum(UserRole, name="userrole_enum"),
        default=UserRole.AGENT,
        server_default=UserRole.AGENT.name,
        nullable=False,
    )
    is_active = Column(Boolean, default=True, nullable=False)
    # Incrementar invalida todos os refresh tokens já emitidos para este usuário. É o que
    # logout e troca de senha fazem — ver `app/api/auth.py`.
    token_version = Column(Integer, default=0, server_default="0", nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )


class UserInvite(Base):
    """Convite pendente para entrar num tenant.

    Guarda só o HASH do token: o link é mostrado uma única vez a quem convidou e não
    pode ser recuperado do banco. Vazamento da tabela não entrega acesso a ninguém.
    """

    __tablename__ = "user_invites"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(
        SAEnum(UserRole, name="userrole_enum"),
        default=UserRole.AGENT,
        server_default=UserRole.AGENT.name,
        nullable=False,
    )
    token_hash = Column(String(64), nullable=False, unique=True)  # sha256 hex
    invited_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_user_invites_tenant", "tenant_id"),
        # Um convite aberto por e-mail dentro do tenant. Convidar de novo substitui o
        # anterior (o admin refaz o link quando a pessoa perde) em vez de acumular
        # tokens válidos esquecidos por aí.
        Index(
            "uq_user_invites_pending",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL"),
        ),
    )


class ChannelConnection(Base):
    __tablename__ = "channel_connections"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel = Column(SAEnum(Channel, name="channel_enum"), nullable=False)
    credentials = Column(JSONB, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Contact(Base):
    __tablename__ = "contacts"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel = Column(SAEnum(Channel, name="channel_enum"), nullable=False)
    external_id = Column(String(255), nullable=False)
    name = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    email = Column(String(255), nullable=True)
    channel_metadata = Column(JSONB, nullable=True)
    # Memória do cliente entre conversas: {intake, last_summary, last_interaction_at,
    # conversations}. Sem isto a IA reencontra um cliente antigo como desconhecido, porque
    # o contexto é carregado por conversation_id e conversa resolvida gera uma nova.
    # Gerido por app/services/contact_memory.py.
    profile = Column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "channel", "external_id", name="uq_contacts_tenant_channel_ext"
        ),
    )


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_connection_id = Column(
        UUID(as_uuid=True),
        ForeignKey("channel_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    status = Column(
        SAEnum(ConversationStatus, name="conversationstatus_enum"),
        nullable=False,
        default=ConversationStatus.OPEN,
    )
    assigned_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    priority = Column(
        String(8), default="normal", nullable=False
    )  # normal | high (triagem do bot)
    tags = Column(
        JSONB, default=list, nullable=False
    )  # etiquetas da conversa (triagem)
    first_response_at = Column(
        DateTime(timezone=True), nullable=True
    )  # 1ª resposta humana (métricas)
    resolved_at = Column(
        DateTime(timezone=True), nullable=True
    )  # quando foi resolvida (métricas)
    closure_reason = Column(String(24), nullable=True)
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    last_message_preview = Column(Text, nullable=True)
    unread_count = Column(Integer, default=0, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_conversations_assignee", "tenant_id", "assigned_user_id"),
        Index("ix_conversations_contact", "tenant_id", "contact_id"),
    )


class Message(Base):
    __tablename__ = "messages"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    direction = Column(
        SAEnum(MessageDirection, name="messagedirection_enum"), nullable=False
    )
    content_type = Column(
        SAEnum(ContentType, name="contenttype_enum"),
        nullable=False,
        default=ContentType.TEXT,
    )
    content = Column(Text, nullable=True)
    external_id = Column(String(255), nullable=True)
    channel = Column(SAEnum(Channel, name="channel_enum"), nullable=False)
    status = Column(SAEnum(MessageStatus, name="messagestatus_enum"), nullable=True)
    sender_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    channel_metadata = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="uq_messages_channel_external"),
        Index("ix_messages_conversation", "conversation_id", "created_at"),
    )


class BotSettings(Base):
    """Config do agente de IA por tenant. persona = COMO atender; knowledge = O QUE vende."""

    __tablename__ = "bot_settings"
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ai_enabled = Column(Boolean, default=False, nullable=False)
    persona = Column(Text, default="", nullable=False)
    knowledge = Column(Text, default="", nullable=False)
    provider = Column(String(32), default="stub", nullable=False)
    model = Column(String(128), default="", nullable=False)
    api_key = Column(Text, default="", nullable=False)  # cifrada em repouso (Fernet)
    transfer_message = Column(Text, default="", nullable=False)
    # Aviso enviado ao cliente quando a equipe confirma a reserva dele (derivado salão).
    # Vazio = usa o padrão de `bot_settings.DEFAULT_CONFIRMACAO_MESSAGE`.
    confirmacao_message = Column(Text, default="", nullable=False)
    use_rag = Column(Boolean, default=False, nullable=False)
    # Qual CÉREBRO atende: 'fluxo' (roteiro determinístico, sem IA) ou 'agente' (LLM).
    # Nasce em 'fluxo': conta nova atende de graça e de forma previsível, e a IA entra
    # por decisão de quem contrata — nunca por padrão esquecido.
    # Combinado com `ai_enabled`, é o controle de 3 estados do painel — desligado, fluxo,
    # agente — e trocar 'agente' por 'fluxo' é o freio de emergência: o custo de LLM cai a
    # zero e o atendimento continua. Consequência de nomenclatura assumida: `ai_enabled`
    # passa a significar "o bot atende", não "usa IA".
    modo = Column(String(16), default="fluxo", server_default="fluxo", nullable=False)
    # Expediente do negócio, lido pelo nó `horario` do fluxo. Formato em
    # `app/flow/horario.py`. NÃO é o padrão semanal que a agenda proibiu: aquela regra é
    # sobre qual DATA está à venda e por quanto; esta é sobre quando existe gente para
    # responder. Perguntas diferentes, tabelas diferentes.
    horario = Column(JSONB, default=dict, nullable=False)
    # Ligado por `scripts/sincronizar_conteudo.py`: persona e knowledge passam a vir
    # de `conteudo/*.md` e a tela os mostra em leitura. Sem isto, editar pelo painel
    # e sincronizar depois se sobrescreveriam em silêncio, nos dois sentidos.
    conteudo_gerenciado = Column(Boolean, default=False, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BotSession(Base):
    """Estado do bot por conversa: o que já foi coletado, falhas e fase de handoff.

    O estado vive no banco (a IA é stateless) — reiniciar worker/API não perde nada.
    """

    __tablename__ = "bot_sessions"
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    variables = Column(JSONB, default=dict, nullable=False)
    ai_failures = Column(Integer, default=0, nullable=False)
    post_handoff_turns = Column(Integer, default=0, nullable=False)
    handoff = Column(Boolean, default=False, nullable=False)
    bot_disabled = Column(Boolean, default=False, nullable=False)
    # Prazo do desligamento, e é ele que separa as DUAS causas de `bot_disabled`:
    #   NULL      → desativado por DECISÃO (teto pós-handoff). Permanente, só liberar reativa.
    #   preenchido→ desativado por FALHA de IA. Volta sozinho quando o prazo vencer.
    # Sem essa distinção as duas causas colapsam e um provedor que caiu por dois minutos
    # mata o agente da conversa para sempre.
    bot_disabled_until = Column(DateTime(timezone=True), nullable=True)
    # Por que o bot não falou na última passagem (vocabulário em bot_orchestrator.SkipReason).
    # NULL = nunca deixou de falar; 'ok' = a última passagem terminou em resposta enviada.
    skip_reason = Column(String(32), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class KbDocument(Base):
    """Base de conhecimento vetorial (Fase 2 do RAG). Cada linha é um CHUNK."""

    __tablename__ = "kb_documents"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    source_url = Column(Text, nullable=True)
    title = Column(Text, nullable=True)
    content = Column(Text, nullable=False)
    # 1536 é o contrato com app.ai.embeddings.EMBED_DIM: os dois provedores recebem
    # `dimensions` explícito para caber aqui (o gemini-embedding-001 é 3072 nativo).
    embedding = Column(Vector(1536), nullable=True)
    # Qual modelo gerou o vetor. A busca filtra por ele: dimensão igual NÃO significa
    # espaço vetorial comparável, então sem isto trocar de modelo devolveria vizinhos
    # sem sentido em vez de erro. Nullable por causa das linhas anteriores à coluna.
    embedding_model = Column(String(64), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (Index("ix_kb_documents_tenant", "tenant_id"),)


class KbSource(Base):
    """Texto ORIGINAL de uma fonte da base de conhecimento.

    A verdade do que o usuário escreveu; `KbDocument` é o índice vetorial derivado
    daqui. Existe porque chunk não remonta documento: o fatiamento usa overlap e não
    guarda ordem, então sem esta tabela o conteúdo era irrecuperável — a base ficava
    write-only e o marcador `stale` pedia uma re-ingestão impossível de cumprir.
    """

    __tablename__ = "kb_sources"
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source = Column(Text, primary_key=True)
    title = Column(Text, nullable=True)
    content = Column(Text, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class QuickReply(Base):
    """Resposta rápida (canned response) do tenant, inserida no ReplyInput via /atalho."""

    __tablename__ = "quick_replies"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    shortcut = Column(String(32), nullable=False)  # ex.: "horario"
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "shortcut", name="uq_quick_replies_tenant_shortcut"
        ),
    )


class ConversationEvent(Base):
    __tablename__ = "conversation_events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    action = Column(
        SAEnum(ConversationEventAction, name="conversationeventaction_enum"),
        nullable=False,
    )
    actor_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    target_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
