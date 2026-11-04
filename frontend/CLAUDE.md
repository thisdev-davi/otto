# CLAUDE.md — front-end do Otto

Este front é **base**: outros sistemas nichados são construídos em cima dele. O que
está aqui é vocabulário compartilhado, não decoração de uma tela.

Antes de mexer em cor, componente ou layout, leia as três regras abaixo.
Para o backend e as regras de produto, ver o `CLAUDE.md` da raiz.

---

## 1. Cor vem de token. Sempre.

**`app/globals.css` é o único arquivo do front onde pode existir hex.** Em componente,
cor entra por classe (`bg-surface`, `text-muted`, `border-line`).

Rodar `npm run check:tokens` reprova hex em classe utilitária, a paleta padrão do
Tailwind (`bg-white`, `text-gray-500`, …) e hex dentro de `style={{}}`.

### O formato e por que ele importa

Os valores são **canais RGB sem `rgb()`** (`--surface: 255 255 255`), e o
`tailwind.config.ts` embrulha em `rgb(var(--x) / <alpha-value>)`.

Sem o `<alpha-value>`, `bg-accent/10` compila para `background-color: var(--accent)`
e **o `/10` some sem erro e sem warning**. Não troque para `var()` cru.

Consequência para cor dinâmica: use `rgb(var(--avatar-3))`, nunca `var(--avatar-3)`
sozinho — neste formato ele não é uma cor válida.

### O vocabulário

| grupo | tokens |
|---|---|
| superfície | `canvas` · `surface` · `surface-raised` · `subtle` |
| borda | `line` · `line-strong` |
| texto | `ink` · `ink-2` · `muted` · `faint` |
| ação | `accent` (+`hover` `soft` `soft-hover` `ring` `fg`) · `on-accent` (+`danger`) |
| semânticos | `success` · `danger` · `warning` · `note` · `ai` · `info`, cada um com `soft` / `line` / `fg` |
| marca e dado | `sky` + `on-sky` (fundo, nunca texto) · `bubble-bot` (+`fg`) · `brand-whatsapp` · `chart-1` `chart-2` · `avatar-1..6` |
| sombra | `shadow-card` `shadow-card-lg` `shadow-pop-sm` `shadow-pop` `shadow-accent` `shadow-success` `shadow-ring` |

**A regra que mantém os pares consistentes:**

- `X` é **preenchimento sólido** — sempre com `on-X` por cima (`bg-accent text-on-accent`).
- `X-fg` é **cor de texto** sobre `surface` ou sobre `X-soft`.

Os sólidos (`accent`, `success`, `danger`) são **iguais nos dois temas**, justamente
porque já passam contraste com texto branco. Só o `-fg` inverte no escuro.

Erro mais comum: usar `text-accent` sobre `bg-accent-soft`. No tema escuro isso é
azul sólido sobre azul escuro — ilegível. O certo é `text-accent-fg`.

### Contraste

`npm run check:contrast` mede 42 pares nos DOIS temas, lendo os valores do próprio
`globals.css`. Mudou paleta, rode. Falhas quebram o comando.

Não afrouxe o mínimo para fazer passar — a paleta atual já custou três correções que
reprovavam antes deste trabalho (`line` a 1,13:1, `faint` a 2,6:1, avatares a 2,6:1).

## 2. Dark mode não se resolve com `dark:`

`dark:` é **sinal de falha da camada de tokens**. Se você está escrevendo um, quase
sempre o token certo não existe ainda — crie o token.

As exceções legítimas não são cor: `components/Logo.tsx` troca o ARQUIVO do símbolo
(azul sobre claro, negativo sobre escuro) com `dark:hidden` / `dark:block`. `grep -rn 'dark:' app components` deve continuar
em dígitos únicos.

Quem pinta o tema é o script inline em `app/layout.tsx`, antes da hidratação. Ele e o
`contexts/ThemeContext.tsx` precisam concordar (mesma chave `otto.theme`, mesma regra).
O tema vai em `<html>` e **nunca** no `<body>` — lá o React é dono do `className`.

## 3. Primitivos antes de markup novo

`components/ui/` é a camada compartilhada. Antes de escrever um botão, campo ou
overlay, use o que já existe:

`button` (cva, variantes `primary`/`secondary`/`destructive`/`success`/`ghost` e
`size: sm|md|icon`) · `input` + `fieldBase` · `textarea` · `select` · `field` ·
`card` · `badge` · `spinner` · `empty-state` · `dialog` · `popover` ·
`dropdown-menu` · `confirm-dialog`.

**Overlay é sempre Radix**, nunca `absolute` + listener de `mousedown`. O que se ganha
não é elegância: é Escape, focus trap, retorno de foco, `aria-*` e **detecção de
colisão** — o popover à mão saía da tela em 375px.

Ação destrutiva usa `ConfirmDialog`. Remover conexão de WhatsApp já foi um clique
único sem pergunta nenhuma.

Variante nova = **string literal** num objeto. O Tailwind lê texto-fonte: `bg-${tom}`
não gera CSS. Pelo mesmo motivo, props de cor são **união fechada** (`tone: 'success'`),
não `string` — ver `components/reports/KpiCard.tsx`.

### A régua visual do Otto

Calmo e denso: fundo liso, cor só onde há ação ou estado.

- **Controles têm 36px** (`sm` 32px) e raio 8. No toque o alvo sobe para 44px pela
  variante `coarse:` (`(pointer: coarse)`, em `tailwind.config.ts`) — não com `min-h`
  fixo, que devolve o botão de 44px ao desktop.
- **Só dois gradientes: o botão primário e a bolha do atendente** (os dois `accent` → `accent-hover`).
  Gradiente em card, fundo ou avatar não entra.
- **Card não tem sombra**: superfície + hairline (`border-line`), raio 12. Sombra é de quem
  flutua: `shadow-soft` no hover de card clicável, `shadow-pop` em menu, popover e diálogo.
- Raios: 12 card · 8 controle · 6 badge e chip · 16 diálogo.
- Campo em foco: borda `accent` + anel de 3px (`fieldBase`).
- Toast é pílula escura (`bg-ink text-surface`); só o de erro é `bg-danger`.
- Movimento: transição de 120ms e clique que afunda (`active:scale-[.985]`), sempre com
  `motion-reduce:` desligando os dois.
- **Tipografia em escala fechada**: `text-2xs` 11 · `xs` 12 · `13` · `sm` 14 · `15` · `base` 16 ·
  `xl` 20 · `2xl` 24 · `3xl` 32. Tamanho novo entra na escala, nunca como `text-[Npx]` solto; os
  `text-[Npx]` antigos migram quando houver lint para isso. `text-2xs`, `text-13` e `text-15` também
  estão em `lib/utils.ts`: escala nova que não passar por lá some no `cn()` (o twMerge a lê
  como cor).
- **Três famílias, cada uma com um papel**: `font-sans` (Geist) é a interface inteira;
  `font-mono` (Geist Mono) é dado — telefone, horário, tempo de espera, métrica — sempre com
  `tabular-nums`; `font-display` (Space Grotesk) só na palavra "Otto", no título do login e
  no h1 de página. Fonte de verdade: tokens e componentes versionados neste repositório.
- **O azul aparece em quatro lugares**: a ação principal da tela, a bolha do atendente, o
  item ativo **do trilho** e o contador de não lidas. Fora disso ele compete com os quatro.
  Submenu (configurações, índice de seções) marca o ativo em neutro: `bg-surface` + hairline.

## 4. Layout

Breakpoints são os padrões do Tailwind. **`lg` (1024px) é a virada da inbox de duas
colunas** — não `md`: sidebar (238px) + lista (300px) deixariam 230px de conversa em
768px.

- O shell mobile mora em `components/AppShell.tsx` (client, segura o drawer).
  `app/(inbox)/layout.tsx` **continua server component** de propósito.
- Abaixo de `lg`, a inbox é mestre-detalhe: lista e thread se revezam, com botão de
  voltar no `ThreadHeader`.
- Altura é `h-screen supports-[height:100dvh]:h-[100dvh]`. `100vh` puro ignora a barra
  de endereço do Safari e, sob `overflow-hidden`, some com o campo de resposta.
- Campo de formulário abaixo de `sm` é **16px** (`text-base sm:text-sm`, já dentro
  de `fieldBase`). Com menos que isso o iOS dá zoom ao focar e não volta.
- Alvo de toque: 44px no toque para todo controle (`coarse:`). Só-ícone é `<Button size="icon">`.
- Conteúdo largo (tabela, gráfico) rola **dentro do próprio container**
  (`overflow-x-auto`). O body nunca rola na horizontal.

## 5. Erro nunca é silencioso

**`.catch(() => {})` é proibido.** Toda chamada de rede tem um destino para a falha, e qual destino
depende do que falhou:

| Situação | Tratamento |
|---|---|
| Falha ao carregar o **conteúdo principal** de uma tela ou painel | **estado do próprio painel**, com "Tentar de novo" (`EmptyState` + `Button`) |
| Falha de uma **ação que o usuário pediu** e não tem lugar na tela | `useToast().error(...)` |
| Falha de **higiene de fundo**, que o usuário não pediu e não pode agir sobre | silêncio — **com um comentário dizendo por quê** |

O único silêncio legítimo hoje é `markRead` em `ConversationThread`: avisar ali dispararia em toda
conversa aberta e treinaria a pessoa a ignorar os avisos que importam.

**Nunca finja sucesso.** O `catch` da lista de conversas fazia `setConversations([])`, e a tela
passava a mostrar "nenhuma conversa" com a API fora do ar — pior que erro, porque o atendente parava
de procurar. Em falha de atualização, **mantenha o que já estava na tela**.

O toast (`contexts/ToastContext.tsx`) não usa Radix, e isso não contradiz o §3: a exigência de Radix
existe por focus trap, Escape, retorno de foco e colisão, e um toast não quer nenhum dos quatro.

---

## Comandos

```bash
npm run dev            # porta 3002
npm run check          # tsc + tokens + contraste — rode antes de commitar
npm run check:tokens   # hex e paleta padrão fora da camada de tokens
npm run check:contrast # WCAG dos tokens, nos dois temas
npm run build
```
