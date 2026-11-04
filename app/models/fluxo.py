"""Modelo do atendimento por fluxo: o roteiro determinístico de um tenant.

Arquivo NOVO de propósito (`docs/produto/PLATAFORMA.md` §1): a coluna `modo` em `bot_settings` é o
único ponto do núcleo que esta feature toca. O grafo em si mora aqui.

**Uma linha por tenant**, igual a `bot_settings`. Vários fluxos por tenant seria a feature
de multi-número (cada número com seu roteiro), que está fora de escopo — e a PK simples é
o que deixa `carregar()` ser um SELECT por chave primária em todo turno do bot.

**O grafo é JSONB, não tabela de nós e arestas.** Ele é lido inteiro por turno, editado
inteiro e publicado inteiro; normalizar daria joins e uma migration por tipo de nó novo,
sem comprar nada. Mesma escolha de `bot_sessions.variables` e `contacts.profile`.

`gerenciado_por_arquivo` espelha `bot_settings.conteudo_gerenciado` e existe pelo mesmo
motivo: com `conteudo/fluxo.json` publicado por `scripts/sincronizar_conteudo.py`, editar
pelo painel e sincronizar depois se sobrescreveriam em silêncio, nos dois sentidos.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.models.core import Base


class BotFlow(Base):
    """Roteiro determinístico do tenant. Ver `app/flow/schema.py` para o formato do grafo."""

    __tablename__ = "bot_flows"
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    nome = Column(Text, default="", nullable=False)
    grafo = Column(JSONB, default=dict, nullable=False)
    # Sobe a cada publicação. Vai para `bot_sessions.variables.fluxo.versao` para que a
    # nota interna consiga dizer em qual versão a conversa estava quando um nó sumiu.
    versao = Column(Integer, default=0, nullable=False)
    gerenciado_por_arquivo = Column(Boolean, default=False, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
