"""Append-only tenant audit. Deliberately contains no message or credential payload."""

import uuid

from sqlalchemy import Column, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.models.core import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No cascading FK: account/user deletion must not rewrite an immutable audit record.
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor_type = Column(String(24), nullable=False)
    actor_user_id = Column(UUID(as_uuid=True), nullable=True)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(64), nullable=False)
    resource_id = Column(String(100), nullable=True)
    result = Column(String(24), nullable=False, server_default="success")
    reason = Column(String(100), nullable=True)
    correlation_id = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    ip_network = Column(String(64), nullable=True)
    details = Column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        Index("ix_audit_tenant_created_id", "tenant_id", "created_at", "id"),
        Index("ix_audit_created", "created_at"),
    )
