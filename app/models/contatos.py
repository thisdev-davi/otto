"""Anotações que a equipe escreve sobre um cliente.

Arquivo NOVO de propósito (`docs/produto/PLATAFORMA.md` §1): `models/core.py` é núcleo, e o que se
quer aqui — uma tabela filha de `contacts` — não exige tocá-lo.

**Por que tabela e não mais uma chave em `contacts.profile`.** O JSONB do perfil já existe
e seria o caminho curto, mas `contact_memory.remember()` o reescreve INTEIRO num
read-modify-write sem lock (SELECT do perfil, merge em Python, UPDATE). Uma anotação
gravada nesse mesmo campo enquanto o agente fecha um turno desaparece sem erro nenhum —
a mesma classe de corrida que as regras 2 e 3 do `CLAUDE.md` proíbem na captura de
conversa e na reserva de data. Linha própria não tem como se perder assim.

O que a tabela ganha de brinde é o que faz a anotação valer numa equipe: **autor e data**.
"Pediu desconto, falei que via com você — Ana, 12/07" é acionável; a mesma frase anônima
num bloco de texto compartilhado não é.
"""

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.models.core import Base


class ContactNote(Base):
    """Uma anotação da equipe sobre um contato. Append-only na prática: cria e apaga.

    `user_id` é `SET NULL` e não `CASCADE`: atendente que sai da equipe não leva junto o
    que ele sabia do cliente. A nota fica, sem autor — é o que se quer, porque o valor
    dela está no texto.
    """

    __tablename__ = "contact_notes"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    texto = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        # A leitura é sempre "notas deste contato, da mais nova para a mais velha".
        Index("ix_contact_notes_contato", "tenant_id", "contact_id", "created_at"),
    )
