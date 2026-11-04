import { useEffect, useRef, useState } from 'react'

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000'

export function useWebSocket(
  token: string | null,
  onMessage: (data: unknown) => void
) {
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  useEffect(() => {
    if (!token) return

    let active = true

    function connect() {
      if (!active) return
      // tenant vem do token (WS autenticado por JWT)
      const ws = new WebSocket(`${WS_URL}/ws?token=${encodeURIComponent(token!)}`)
      wsRef.current = ws

      ws.onopen = () => {
        if (active) setConnected(true)
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          onMessageRef.current(data)
        } catch {
          // ignore parse errors
        }
      }

      ws.onclose = () => {
        if (active) {
          setConnected(false)
          reconnectTimeout.current = setTimeout(connect, 3000)
        }
      }

      ws.onerror = () => {
        ws.close()
      }
    }

    connect()

    return () => {
      active = false
      if (reconnectTimeout.current) clearTimeout(reconnectTimeout.current)
      wsRef.current?.close()
      setConnected(false)
    }
  }, [token])

  return { connected }
}
