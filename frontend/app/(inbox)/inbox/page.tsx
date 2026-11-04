import { Suspense } from 'react'
import InboxView from '@/components/InboxView'

// `InboxView` lê `useSearchParams` (deep link `?c=<id>`, usado pelo botão "Abrir a
// conversa" da Agenda), e o Next 15 exige um limite de Suspense em volta disso — sem ele
// quebra o `next build`, não o runtime, então só aparece no deploy.
export default function InboxAllPage() {
  return (
    <Suspense>
      <InboxView queue="all" />
    </Suspense>
  )
}
