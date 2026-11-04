"""Modelos do derivado Salão de Festas: agenda de datas, reservas e galeria do espaço.

Arquivo NOVO de propósito (`PLATAFORMA.md` §1): nada aqui altera `models/core.py`, então
este derivado não conflita com a base no arquivo mais sensível do núcleo.

A unidade de negócio é a **data inteira** — o salão é alugado por dia, não por período.
Por isso `salao_datas` tem uma linha por dia e `salao_reservas` no máximo uma reserva
ativa por dia.

**O cadastro é por data, e só.** Uma regra por dia da semana chegou a existir aqui e foi
retirada por decisão de produto: num salão de festas o preço é ditado por feriado e data
comemorativa, que são conjuntos irregulares, e um padrão semanal virava mais uma camada
para conferir. Quem preenche muitas datas de uma vez usa a seleção múltipla do calendário.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.models.core import Base


class ReservaStatus:
    """Vocabulário fechado de `salao_reservas.status`.

    String e não Enum do Postgres: valor novo aqui é uma constante Python, não uma
    migration de `ALTER TYPE`. Mesma escolha já feita em `conversations.priority`.

    Só PRE_RESERVADA e CONFIRMADA ocupam a data — é exatamente essa a condição do índice
    parcial `uq_salao_reservas_data_ativa`.
    """

    PRE_RESERVADA = "pre_reservada"
    CONFIRMADA = "confirmada"
    CANCELADA = "cancelada"
    EXPIRADA = "expirada"

    ATIVOS = (PRE_RESERVADA, CONFIRMADA)


class SalaoData(Base):
    """Um dia do calendário: se está à venda e por quanto.

    `preco_centavos` NULL não é "de graça" — é **valor não cadastrado**. O agente trata a
    ausência como desconhecimento e manda confirmar com um atendente, nunca estima. Para
    tirar da venda uma data já precificada existe `bloqueada`, que é explícito e
    reversível, ao contrário de apagar o valor.
    """

    __tablename__ = "salao_datas"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    data = Column(Date, nullable=False)
    preco_centavos = Column(Integer, nullable=True)
    # Manutenção, uso próprio, férias: some da disponibilidade sem virar reserva de cliente.
    bloqueada = Column(Boolean, default=False, nullable=False)
    observacao = Column(Text, nullable=True)  # nota do dono, nunca vai ao cliente
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
        UniqueConstraint("tenant_id", "data", name="uq_salao_datas_tenant_data"),
    )


class SalaoReserva(Base):
    """A ocupação de uma data. Uma ativa por data, garantido pelo banco.

    O índice parcial `uq_salao_reservas_data_ativa` é a peça central do recurso: ele cobre
    só `pre_reservada` e `confirmada`, então reserva cancelada ou expirada continua no
    banco como histórico **sem** segurar a data.

    Mesmo princípio da captura de conversa (`CLAUDE.md` §2): quem arbitra a corrida é o
    banco. Nunca ler as reservas, decidir em Python que a data está livre e inserir —
    isso reabre o overbooking entre o SELECT e o INSERT.
    """

    __tablename__ = "salao_reservas"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    data = Column(Date, nullable=False)
    status = Column(String(16), nullable=False, default=ReservaStatus.PRE_RESERVADA)
    contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    criada_por = Column(String(8), nullable=False, default="humano")  # bot | humano
    criada_por_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Só faz sentido em pre_reservada. Vencida, a reserva é marcada EXPIRADA na próxima
    # leitura ou disputa da data — expiração preguiçosa, sem `celery beat` para manter.
    expira_em = Column(DateTime(timezone=True), nullable=True)
    # Cópia no momento da reserva: o contato pode ser renomeado ou apagado depois, e a
    # agenda precisa continuar dizendo de quem era a data.
    nome_cliente = Column(String(255), nullable=True)
    telefone = Column(String(50), nullable=True)
    observacao = Column(Text, nullable=True)
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
        Index(
            "uq_salao_reservas_data_ativa",
            "tenant_id",
            "data",
            unique=True,
            postgresql_where=text("status IN ('pre_reservada', 'confirmada')"),
        ),
        Index("ix_salao_reservas_tenant_data", "tenant_id", "data"),
    )


class SalaoFoto(Base):
    """Foto do espaço, na galeria que o agente manda quando o cliente pede para ver.

    O arquivo vive em `MEDIA_DIR` (mesma raiz da mídia de conversa); aqui só o caminho.
    `ativa=false` tira da rotação do agente sem apagar o arquivo — desligar uma foto que
    ficou ruim não pode custar o upload de novo.
    """

    __tablename__ = "salao_fotos"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    titulo = Column(String(255), nullable=True)
    caminho = Column(Text, nullable=False)
    mimetype = Column(String(64), nullable=False)
    tamanho_bytes = Column(Integer, nullable=False, default=0)
    ordem = Column(Integer, nullable=False, default=0)  # o agente manda nesta ordem
    ativa = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (Index("ix_salao_fotos_tenant", "tenant_id", "ordem"),)
