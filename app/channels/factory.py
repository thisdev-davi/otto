from app.channels.base import ChannelProvider
from app.channels.whatsapp import EvolutionProvider
from app.models.enums import Channel

# Único ponto de troca de provider por canal (CLAUDE.md §1).
# MVP: só WhatsApp ativo. Registrar outros canais aqui quando forem retomados.
_registry: dict[Channel, type[ChannelProvider]] = {
    Channel.WHATSAPP: EvolutionProvider,
}


def get_provider(channel: Channel) -> ChannelProvider:
    cls = _registry.get(channel)
    if cls is None:
        raise ValueError(f"Provider não registrado para canal: {channel}")
    from app.channels.audited import AuditedProvider

    return AuditedProvider(cls())
