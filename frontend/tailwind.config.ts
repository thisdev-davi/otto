import type { Config } from 'tailwindcss'

// Os valores moram em app/globals.css, como canais RGB. O <alpha-value> é o que
// mantém os modificadores de opacidade funcionando (bg-accent/10, text-on-accent/65);
// com `var(--x)` cru o Tailwind descarta o modificador sem avisar.
const rgb = (name: string) => `rgb(var(${name}) / <alpha-value>)`

const config: Config = {
  // 'class' e não 'media': o usuário pode forçar claro/escuro contra a preferência
  // do sistema. Quem escreve a classe é o script anti-flash em app/layout.tsx.
  darkMode: 'class',
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    // Escala FECHADA (substitui a do Tailwind, não estende): 11·12·13·14·15·16·20·24·32.
    // `2xs`, `13` e `15` também estão em lib/utils.ts — sem isso o twMerge lê `text-13` como
    // cor e descarta um dos dois quando o componente junta `text-13 text-ink`.
    fontSize: {
      '2xs': ['11px', '16px'],
      xs: ['12px', '16px'],
      '13': ['13px', '20px'],
      sm: ['14px', '20px'],
      '15': ['15px', '22px'],
      base: ['16px', '24px'],
      xl: ['20px', '28px'],
      '2xl': ['24px', '32px'],
      '3xl': ['32px', '38px'],
    },
    extend: {
      // `coarse:` = dedo. Os controles têm 36px no desktop e 44px de alvo no toque.
      screens: {
        coarse: { raw: '(pointer: coarse)' },
      },
      fontFamily: {
        sans: ['var(--font-geist)', 'system-ui', 'sans-serif'],
        // Dado: telefone, horário, métrica. Sempre com `tabular-nums`.
        mono: ['var(--font-geist-mono)', 'ui-monospace', 'monospace'],
        // Marca e título de página: a palavra "Otto", o login, o h1. Nada mais.
        display: ['var(--font-space-grotesk)', 'var(--font-geist)', 'sans-serif'],
      },
      colors: {
        canvas: rgb('--canvas'),
        surface: {
          DEFAULT: rgb('--surface'),
          raised: rgb('--surface-raised'),
        },
        subtle: rgb('--subtle'),
        overlay: rgb('--overlay'),
        line: {
          DEFAULT: rgb('--line'),
          strong: rgb('--line-strong'),
        },

        ink: {
          DEFAULT: rgb('--ink'),
          2: rgb('--ink-2'),
        },
        muted: rgb('--muted'),
        faint: rgb('--faint'),

        accent: {
          DEFAULT: rgb('--accent'),
          hover: rgb('--accent-hover'),
          soft: rgb('--accent-soft'),
          'soft-hover': rgb('--accent-soft-hover'),
          ring: rgb('--accent-ring'),
          fg: rgb('--accent-fg'),
        },
        'on-accent': {
          DEFAULT: rgb('--on-accent'),
          danger: rgb('--on-accent-danger'),
        },
        'on-success': rgb('--on-success'),
        'on-danger': rgb('--on-danger'),

        success: {
          DEFAULT: rgb('--success'),
          soft: rgb('--success-soft'),
          line: rgb('--success-line'),
          fg: rgb('--success-fg'),
        },
        danger: {
          DEFAULT: rgb('--danger'),
          hover: rgb('--danger-hover'),
          soft: rgb('--danger-soft'),
          line: rgb('--danger-line'),
          fg: rgb('--danger-fg'),
        },
        warning: {
          DEFAULT: rgb('--warning'),
          soft: rgb('--warning-soft'),
          line: rgb('--warning-line'),
          fg: rgb('--warning-fg'),
        },
        note: {
          soft: rgb('--note-soft'),
          line: rgb('--note-line'),
          fg: rgb('--note-fg'),
        },
        ai: {
          DEFAULT: rgb('--ai'),
          soft: rgb('--ai-soft'),
          fg: rgb('--ai-fg'),
        },
        info: rgb('--info'),
        sky: rgb('--sky'),
        'on-sky': rgb('--on-sky'),
        'bubble-bot': {
          DEFAULT: rgb('--bubble-bot'),
          fg: rgb('--bubble-bot-fg'),
        },

        brand: {
          whatsapp: rgb('--brand-whatsapp'),
        },
        chart: {
          1: rgb('--chart-1'),
          2: rgb('--chart-2'),
        },
      },
      boxShadow: {
        card: 'var(--shadow-card)',
        soft: 'var(--shadow-soft)',
        'card-lg': 'var(--shadow-card-lg)',
        'pop-sm': 'var(--shadow-pop-sm)',
        pop: 'var(--shadow-pop)',
        accent: 'var(--shadow-accent)',
        success: 'var(--shadow-success)',
        ring: '0 0 0 4px rgb(var(--accent-ring))',
      },
    },
  },
  plugins: [],
}

export default config
