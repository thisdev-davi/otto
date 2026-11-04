import type { NextConfig } from 'next'

/**
 * Cabeçalhos de segurança. O arquivo estava vazio — nenhum header, nenhuma CSP.
 *
 * A CSP importa aqui mais do que o normal porque os dois tokens vivem no `localStorage`
 * (ver contexts/AuthContext.tsx): qualquer script de terceiro que execute na origem do
 * painel entrega a sessão. Enquanto os tokens estiverem ali, a CSP é a principal defesa
 * que sobra.
 *
 * `'unsafe-inline'` em script-src é exigido pelo Next em produção (bootstrap de
 * hidratação, e o themeScript inline de app/layout.tsx que pinta o tema antes da
 * hidratação). Trocar por nonce obriga o app a renderizar dinâmico — vale a pena, mas é
 * mudança de arquitetura, não de configuração.
 *
 * `connect-src`, `img-src` e `media-src` precisam citar a API: ela mora em outra origem
 * (porta 8001) e é de lá que vêm mídia da thread e fotos do espaço.
 */
const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const WS = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000'

// `next dev` embrulha cada módulo em `eval()` (React Refresh) e abre um WebSocket de HMR
// na própria origem. Sem as duas liberações abaixo, NENHUM script do painel executa em
// desenvolvimento: a página monta o HTML, não hidrata, e o formulário de login volta a ser
// submit nativo — a tela recarrega e ninguém entra. Em produção não há eval nem HMR, então
// as liberações ficam de fora do build, que é onde a CSP precisa valer.
const dev = process.env.NODE_ENV !== 'production'

const csp = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ''}`,
  "style-src 'self' 'unsafe-inline'",
  // blob:/data: para pré-visualizar o anexo antes de subir.
  `img-src 'self' data: blob: ${API}`,
  `media-src 'self' blob: ${API}`,
  `connect-src 'self' ${API} ${WS}${dev ? ' ws://localhost:* ws://127.0.0.1:*' : ''}`,
  "font-src 'self' data:",
].join('; ')

const nextConfig: NextConfig = {
  distDir: process.env.NEXT_DIST_DIR || '.next',
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'Content-Security-Policy', value: csp },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          {
            key: 'Permissions-Policy',
            value: 'camera=(), microphone=(), geolocation=()',
          },
          // Quem termina o TLS é o nginx, então normalmente o HSTS sai de lá. Repetir
          // aqui cobre o caso de o proxy ser trocado sem alguém lembrar deste header.
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=31536000; includeSubDomains',
          },
        ],
      },
    ]
  },
}

export default nextConfig
