// Metadados de canal — fonte única. MVP: só WhatsApp.
export type ChannelKey = 'whatsapp'

// `dotClass` em vez de hex: a cor entra por classe do Tailwind, então o ponto de
// canal segue o tema como qualquer outro elemento. O verde do WhatsApp é marca e
// por isso é o único token que NÃO muda no escuro (ver --brand-whatsapp).
export const CHANNEL_META: Record<ChannelKey, { label: string; dotClass: string }> = {
  whatsapp: { label: 'WhatsApp', dotClass: 'bg-brand-whatsapp' },
}

export function channelDotClass(key: string | null | undefined): string {
  return CHANNEL_META[(key ?? '') as ChannelKey]?.dotClass ?? 'bg-faint'
}

export function channelLabel(key: string | null | undefined): string {
  return CHANNEL_META[(key ?? '') as ChannelKey]?.label ?? (key ?? '—')
}
