import { Suspense } from 'react'
import InboxView from '@/components/InboxView'

// Suspense pelo `useSearchParams` de `InboxView` — ver `app/(inbox)/inbox/page.tsx`.
export default function InboxUnclaimedPage() {
  return (
    <Suspense>
      <InboxView queue="unclaimed" />
    </Suspense>
  )
}
