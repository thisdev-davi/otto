# Otto — Documentação do Projeto

> Plataforma de atendimento para pequenos negócios.
> Caixa de entrada única de WhatsApp, com captura de conversas em equipe e agente de IA.

---

## 1. Visão e posicionamento

**O que é.** Otto é uma inbox de atendimento com agente de IA. O dono de um pequeno negócio (salão, clínica, loja) conecta seu WhatsApp e atende tudo numa só tela, com a IA fazendo a primeira interação e passando para um humano quando precisa.

**A dor.** Negócios pequenos atendem clientes em vários apps ao mesmo tempo e não dão conta: conversas se perdem, ninguém sabe quem respondeu o quê, o cliente fica sem resposta.

**Posicionamento.** Público-alvo: pequenos negócios no Brasil; o derivado publicado aqui é um salão de festas. Foco em simplicidade, preço acessível e onboarding self-service de minutos — sem etapa de "fale com o consultor".

---

## 2. Decisões estratégicas

### 2.1 Stack

- **Backend:** FastAPI + Celery.
- **Frontend:** Next.js 15.5 + TypeScript. Turbopack é o bundler padrão; requer Node 20+.
- **Acoplamento front/back:** o cliente TypeScript é **escrito à mão** (`frontend/lib/api.ts`, `frontend/types/api.ts`), espelhando os schemas Pydantic. Não há codegen: mudança de schema exige atualizar o tipo TS na mão. O `/openapi.json` do FastAPI serve de conferência, não de fonte.

### 2.2 WhatsApp — Evolution API (baileys)

**Decisão (2026-07-25):** o produto fica na **Evolution API com baileys**. A migração para a Cloud API
Oficial da Meta sai do roadmap ativo — `CloudAPIProvider` continua como esqueleto em
`channels/whatsapp.py`, porta de saída caso a Evolution se torne inviável, não plano em execução.

**Restrições a respeitar na implementação:**
- A camada de WhatsApp continua sendo um **adapter** (ver §4.2), e a regra de isolamento do
  `CLAUDE.md` §1 **permanece valendo** — não porque uma migração está marcada, mas porque é ela que
  mantém a saída barata. Nenhum import direto de Evolution fora de `channels/whatsapp.py`.
- No Otto o WhatsApp **é o produto** (não uma feature secundária).

**O que a Evolution/baileys implica e não pode ser esquecido:** é conexão não-oficial via aparelho
pareado — sujeita a ban, a limite de aparelhos e a entregar backlog ao reconectar (o incidente que
gerou a guarda anti-flood em `bot_orchestrator._is_stale`).

### 2.3 Agente de IA — entregue

O agente que faz a primeira interação com o cliente é o diferencial de longo prazo. A decisão original
foi deixá-lo **fora do MVP** para que o canal provasse a dor primeiro; o modelo de dados já nasceu com
a porta aberta (mensagem `OUTBOUND` com `sender_user_id` nulo = envio do sistema/agente).

Está entregue desde a fase 2, com persona e conhecimento por tenant, RAG e handoff — ver §3.1. O
conteúdo que o alimenta tem guia próprio: `docs/guias/GUIA-CONTEUDO-AGENTE.md`.

---

## 3. Escopo

### 3.1 Entregue

- Inbox de WhatsApp (Evolution/baileys) com múltiplos atendentes e multi-tenant.
- **Captura de conversa**: atendente puxa da fila; conversa capturada some da fila dos outros.
- Atribuição, liberação e transferência (endpoint atômico + seletor de destinatário na thread).
- **Gestão de equipe**: convite por link, papéis ADMIN/AGENT, promoção e desativação de membro.
- Atualização em tempo real (WebSocket autenticado por JWT).
- Mídia nos **dois sentidos**: imagem/áudio/documento recebidos são exibidos e tocados na thread
  (**áudio transcrito** vira texto no contexto), e o atendente envia anexo pelo campo de resposta.
- **Busca de conversas** por nome do contato, telefone e última mensagem, nas três filas.
- **Respostas rápidas**: tela de gestão (admin) + inserção pelo atendente digitando `/` na conversa.
- Agente de IA: persona + conhecimento por tenant, handoff com nota interna, guarda anti-flood,
  copilot de sugestão para o atendente, e RAG sobre base de conhecimento vetorial (pgvector).
- **Memória de cliente entre conversas** (`contacts.profile`): o que a IA já coletou sobrevive à
  conversa resolvida e aparece para quem assume o atendimento seguinte.
- Relatórios: KPIs, volume, desempenho por atendente e do bot.
- Front-end como base de plataforma: tokens de cor em CSS vars, tema escuro, primitivos em
  `components/ui/`, layout de 320px ao desktop. Convenções em `frontend/CLAUDE.md`.
- **O Otto é base para derivados**: cada sistema nichado é um fork, com seu deploy e sua persona.
  A fronteira entre núcleo estável e ponto de extensão está em `docs/produto/PLATAFORMA.md`.
- **Agenda do salão (deste derivado)**: preço por data, bloqueio, preenchimento em lote e
  **pré-reserva atômica** — overbooking impossível por construção, via índice parcial único
  (`CLAUDE.md` §3). O agente consulta a agenda em tempo real e informa o valor exato da data;
  data não cadastrada ele trata como desconhecida e encaminha, em vez de estimar. Pode segurar
  uma data por 48h; quem confirma é sempre humano.
- **Atendimento por fluxo (nível 1)**: roteiro determinístico sem nenhuma chamada de LLM,
  como segundo cérebro dentro do mesmo orquestrador — mesmo contrato de decisão, mesmas
  guardas, mesmo envio (`CLAUDE.md` §7). Vem com validador que reprova roteiro quebrado
  antes de publicar, tela de visualização das caixas conectadas, horário de atendimento por
  tenant e um controle de 3 estados no painel (desligado · fluxo · agente) que serve de
  freio de emergência: trocar IA por roteiro é um clique e o custo cai a zero.
- **Edição do texto do roteiro pelo painel** e **simulador**: o cliente muda o que a caixa
  diz sem tocar no desenho — o servidor aplica só texto sobre o grafo publicado, então não
  existe requisição capaz de quebrar a estrutura. E o simulador roda a conversa inteira
  contra a agenda real sem enviar nada nem segurar data, que é o que torna editar seguro e
  o que permite demonstrar o produto sem parear um número.
- **Galeria do espaço (deste derivado)**: fotos cadastradas com ordem e liga/desliga, que o
  agente envia sozinho quando o cliente pede para ver o salão. Conteúdo em `docs/guias/GUIA-PERSONA-SALAO.md`.
- **Nenhum caminho termina em silêncio**: mídia que o bot não lê é reconhecida por tipo e, na
  segunda, vira fila da equipe com nota — nos dois cérebros; áudio é transcrito e respondido pelo
  conteúdo, e transcrição que falha também avisa. Reação (👍) deixou de virar mensagem.
- **A Inbox mostra o estado real da entrega** (✓ enviado · ✓✓ entregue · lido · falhou), lendo o
  ACK do canal em vez de assumir sucesso no HTTP do provedor.

### 3.2 Fora de escopo (decidido, não "adiado indefinidamente")

- **Instagram DM e e-mail** — removidos do código em 2026-07-24. O foco é 100% WhatsApp. A interface
  `ChannelProvider` permanece como ponto de extensão; re-adicionar canal = re-registrar na factory.
- **Cloud API Oficial da Meta** — ver §2.2. Ficamos na Evolution.
- **Deploy executado.** Existe CI (`.github/workflows/ci.yml`: lint, testes com cobertura,
  checagens do front e gitleaks) e um `render.yaml` preparado com o roteiro em `docs/DEPLOY.md`,
  mas nenhuma implantação remota foi feita: a forma certa de hospedar é decisão de quem vai a
  produção (`docs/produto/PLATAFORMA.md` §0).
- **Editor visual de fluxo** (arrastar, conectar e criar caixas). Editar o **texto** das
  caixas entregou junto com o motor; o que continua fora é mexer no *desenho* pelo painel.
  Quais caixas existem e como se ligam vem de `conteudo/fluxo.json`, versionado no git e
  publicado pelo sincronizador — mesmo caminho da persona, mesmo validador. É uma escolha, e
  não uma pendência: é ela que impede um cliente de criar aresta solta ou apagar um ramo de
  motivo. Arrastar e conectar volta à mesa quando a demanda aparecer.
- **Filas por setor.** Considerada e recusada em 2026-09-02: resolve "quem pega esta
  conversa" numa operação de dez atendentes, e o cliente deste produto tem dois. Entraria
  como um filtro que ninguém usa e uma coluna nas três consultas mais quentes da inbox.
- Funil de vendas, campanhas em massa.
- Distribuição automática de conversas (round-robin entre atendentes online).
- Merge de contatos.

### 3.3 Pendências priorizadas

Revisadas em 2026-07-27 com nota de 0 a 5 e corte em 4 — o que passou do corte está em §3.1.
As notas ficam registradas porque a razão da recusa envelhece melhor que a recusa.

1. **Cobertura de teste de `tasks/media.py`** (3) — o caminho que toca disco e rede externa. A
   rota de webhook já tem teste próprio (`tests/test_webhook_auth.py`), inclusive de isolamento
   entre conexões.
2. **Compreensão de imagem** (3) — imagem é baixada, exibida e nunca interpretada. Os dois cérebros
   avisam que não leem e, na segunda, chamam gente (`services/midia.py`). É expansão de capacidade,
   não buraco: quem lê a imagem hoje é o humano que assume, e agora ele é sempre chamado.
3. **RLS só em `kb_documents`** — o isolamento entre tenants das demais tabelas depende 100% da
   disciplina de incluir `tenant_id` no `WHERE`. Basta para um derivado de tenant único; para um que
   atenda vários clientes no mesmo banco, a migration de RLS é decisão dele. Ver `docs/produto/PLATAFORMA.md` §2.3.

**Resolvido em 2026-09-08:** a **chave de transcrição** deixou de ser pendência — o último ramo de
`transcription._resolve` cai na chave do próprio provedor do tenant (Gemini transcreve por
`generateContent`), então uma chave só cobre conversa e áudio. E a **entrega no WhatsApp** deixou
de ser cega: `messages.update` volta a ser lido (o payload do Evolution é plano, `data.keyId`, e
lê-lo como aninhado descartava todo ACK em silêncio), então `DELIVERED`/`READ`/`FAILED` chegam à
thread. Enquanto isso não existia, `SENT` era só "o Evolution aceitou" e mensagem recusada pelo
WhatsApp aparecia para o atendente como entregue.

**Resolvido nesta leva:** a superfície de erro do front (era o item 3, nota 3). Os nove sítios que
engoliam falha de rede agora reportam — falha de conteúdo principal vira estado do painel com
"Tentar de novo", ação do usuário vira aviso, e o único silêncio que restou (`markRead`) tem o porquê
escrito. A regra ficou em `frontend/CLAUDE.md` §5, para o próximo derivado herdar a convenção e não
só o código.

**Histórico por contato** saiu desta lista: foi resolvido por outro caminho que o texto original
previa. Em vez de carregar o histórico de mensagens entre conversas, `app/services/contact_memory.py`
mantém um perfil estruturado no contato (`intake`, `last_summary`, contador de conversas) que a IA
alimenta e o atendente vê no topo da thread. Ver §5.3.

---

## 4. Arquitetura

### 4.1 Visão geral

```
Canal externo            Ingestão              Núcleo                 Frontend
─────────────────        ─────────────         ─────────────          ─────────────
WhatsApp (Evolution) ───> Webhooks (FastAPI) ─> Worker (Celery) ─┐
   (único ativo)               │                                 │
                               │                                 ├─> PostgreSQL
                          verify_webhook                         │   (inbox + pgvector)
                          parse_webhook                          │
                               │                                 ├─> Redis (pub/sub)
                               v                                 │      │
                       NormalizedInbound ──> Serviço de          │      v
                                             ingestão ───────────┘  WebSocket ─> Next.js
                                                                                  (inbox em
                                                                                   tempo real)
```

O coração é uma **caixa de entrada em tempo real**. O núcleo nunca vê o formato do canal: o provider traduz o payload bruto em `NormalizedInbound` antes de qualquer coisa tocá-lo. Hoje só o WhatsApp está registrado, mas o modelo de "conversa" e "mensagem" continua neutro — é isso que mantém a troca de provider barata (§2.2).

### 4.2 Adapter de canais (a peça central)

Tudo gira em torno da interface `ChannelProvider`, com três responsabilidades:

| Método | Função |
|--------|--------|
| `parse_webhook(payload)` | Traduz o payload bruto do canal em `NormalizedInbound[]` |
| `send(message)` | Traduz `OutboundMessage` na chamada de API do canal |
| `verify_webhook(headers, body)` | Valida que o webhook veio mesmo do provedor |

O núcleo do Otto **nunca** importa a Evolution diretamente. Ele pede um provider à factory e fala com a interface. Com um só canal registrado isso parece cerimônia — não é: é o que protege contra a dependência de uma conexão não-oficial que pode ser banida.

**Tipos normalizados** (a única forma do núcleo falar com canais):
- `NormalizedInbound` — mensagem recebida, já traduzida.
- `OutboundMessage` — pedido de envio no formato do Otto.
- `SendResult` — resultado do envio (sucesso + `external_id` para gravar).

### 4.3 Stack de runtime

| Camada | Tecnologia | Observação |
|--------|-----------|------------|
| API / webhooks | FastAPI | Async ponta a ponta |
| Workers | Celery | Ingestão, bot e mídia fora do request |
| Tempo real | WebSocket (FastAPI nativo) + Redis pub/sub | Novo no Otto; a inbox exige |
| Banco | PostgreSQL | Multi-tenant por linha (`tenant_id`) |
| ORM | SQLAlchemy + Alembic | Híbrido com SQL puro onde a concorrência exige |
| Frontend | Next.js 15.5 + TS + Tailwind + primitivos próprios (cva + Radix) | Ver `frontend/CLAUDE.md` |

---

## 5. Modelo de dados

### 5.1 Princípio

O núcleo é **agnóstico de canal**. Tudo específico de um canal vive em duas válvulas de escape: o campo `channel` (origem) e campos JSONB `channel_metadata` (o que é específico, sem poluir o schema). Adicionar um canal **nunca** exige migração de schema.

### 5.2 Hierarquia

```
Tenant (negócio)
  ├── ChannelConnection   contas conectadas (hoje: número(s) de WhatsApp)
  ├── Contact             o cliente, deduplicado por (tenant, canal, identificador)
  │     └── Conversation  uma thread de atendimento; é a unidade da fila da inbox
  │           ├── Message            cada mensagem trocada
  │           └── ConversationEvent  histórico append-only de "quem mexeu"
  └── User                atendentes
```

### 5.3 Decisões que importam

**Idempotência de mensagens.** `Message` tem `UniqueConstraint(channel, external_id)`. Webhooks são reenviados o tempo todo; essa constraint impede que o mesmo evento vire duas mensagens. Crítico — dói muito se descoberto só em produção.

**Desnormalização proposital na `Conversation`.** `last_message_at`, `last_message_preview` e `unread_count` ficam na própria conversa para ordenar e renderizar a lista da inbox sem JOIN/agregação a cada carregamento.

**Deduplicação de contato por canal.** A mesma pessoa em dois canais vira dois contatos por padrão. Unificá-los (merge) é problema de fase posterior — e, com o produto só em WhatsApp, hoje não acontece.

**Contato é durável, conversa é episódica.** O `Contact` é feito upsert por `(tenant_id, channel, external_id)` — o número — e o nome é atualizado quando o WhatsApp manda um diferente. Já a `Conversation` é reaproveitada só enquanto não está `RESOLVED`: cliente que volta depois de resolvida abre uma conversa nova. **Consequência hoje:** `ai_context.load_history` filtra por `conversation_id`, então a IA atende quem já é cliente como se fosse a primeira vez. O histórico existe no banco, ligado ao contato — só não é carregado. Ver §3.3, item 2.

---

## 6. Captura de conversas em equipe

Este é o mecanismo que faz a inbox funcionar com vários atendentes: quando alguém **captura** uma conversa, ela sai da fila dos outros, evitando resposta dobrada.

### 6.1 A "fila" é uma consulta, não uma tabela

Três visões da mesma tabela `conversations`:

| Visão | Filtro | Para quem |
|-------|--------|-----------|
| Fila geral | `assigned_user_id IS NULL` | todos disputam |
| Minhas conversas | `assigned_user_id = :eu` | cada atendente |
| Todas | sem filtro de dono | gestor |

**"Sumir da fila" ≠ "ninguém mais pode ver".** Conversa capturada some da fila geral, mas o gestor continua vendo tudo (precisa, para acompanhar e remanejar). Juntar os dois conceitos agora geraria retrabalho depois.

Índice de apoio: `ix_conversations_assignee (tenant_id, assigned_user_id)`.

### 6.2 O race condition da captura (e a solução)

**O problema.** Duas atendentes veem a mesma conversa livre e clicam "capturar" no mesmo instante. Implementação ingênua (ler → ver que está NULL → escrever meu id) deixa as duas lerem NULL e escreverem: a última vence, as duas acham que pegaram, o cliente recebe resposta dobrada.

**A solução.** UPDATE **atômico e condicional**, delegando a corrida ao banco:

```sql
UPDATE conversations
   SET assigned_user_id = :user_id, assigned_at = now(), status = 'pending'
 WHERE id = :conversation_id
   AND tenant_id = :tenant_id
   AND assigned_user_id IS NULL    -- só captura se ainda estiver livre
RETURNING id;
```

Das duas requisições simultâneas, **uma** casa com `IS NULL` e captura; a outra não afeta linha alguma (`RETURNING` vazio) e sabe que perdeu — a UI mostra "fulana já pegou esta conversa". É um caso onde o SQL explícito é obrigatório: o caminho ORM ingênuo reabre a brecha.

A mesma lógica protege **liberação** (`AND assigned_user_id = :user_id` — só o dono libera) e **transferência** (`AND assigned_user_id = :from_user_id` — só transfere se ainda for o dono).

### 6.3 Histórico append-only

`ConversationEvent` registra cada ação (`CLAIMED`, `RELEASED`, `TRANSFERRED`, `RESOLVED`, `REOPENED`) com ator, alvo e timestamp. O `assigned_user_id` diz quem é o dono **agora**; os eventos contam **como se chegou lá**. Resolve discussão entre atendentes e alimenta métrica de produtividade. O evento e a mudança de estado entram na **mesma transação** (atomicidade).

`assigned_at` na `Conversation` habilita alertas de "parada há X minutos" e relatórios de tempo de resposta — o dado precisa existir desde já, ou o passado não se reconstrói.

---

## 7. Segurança e conformidade

- **Verificação de webhook — duas barreiras.** A Evolution não assina o payload, então a `apikey` compartilhada (comparada com `hmac.compare_digest`) prova só a origem. O tenant sai do `webhook_id` secreto **por conexão** no caminho da URL (`services/channels.py::resolve_by_webhook_id`); o `instance` do corpo é apenas conferido. Detalhes e histórico em `SEGURANCA.md`.
- **Credenciais de canal.** Guardadas em `ChannelConnection.credentials` (JSONB). Nunca logar; criptografar em repouso em produção.
- **Isolamento multi-tenant.** `tenant_id` em todas as queries de captura como defesa em profundidade — impede acessar conversa de outro tenant mesmo que um id vaze.
- **LGPD.** Atendimento lida com dados pessoais de clientes; tratar retenção, exportação e exclusão como requisito desde a modelagem.

---

## 8. Roadmap

| Fase | Entrega | Estado |
|------|---------|--------|
| MVP | Inbox WhatsApp + captura em equipe + multi-tenant + tempo real | ✓ entregue |
| Fase 2 | Agente de IA para primeira interação + RAG + copilot | ✓ entregue |
| Fase 3 | Gestão de equipe, memória de cliente, front-end como base de plataforma | ✓ entregue |
| Fase 4 | Envio de mídia pelo atendente, respostas rápidas gerenciáveis, busca de conversas | ✓ entregue |
| Fase 5 | Base para derivados: `docs/produto/PLATAFORMA.md`, README, superfície de erro no front | ✓ entregue |
| Derivado | Salão de festas: agenda com preço por data, pré-reserva atômica, galeria do espaço | ✓ entregue |
| Nível 1 | Atendimento por fluxo (sem IA), horário de atendimento, killswitch de 3 estados | ✓ entregue |
| Nível 1 | Edição do texto das caixas pelo painel + simulador do roteiro | ✓ entregue |
| Produção | Entrega verificada por ACK, ordem das bolhas, mídia nos dois cérebros, conta nova em fluxo | ✓ entregue |
| Qualidade | Avaliação offline do agente (`evals/`), cobertura na CI, ESLint e teste no front | ✓ entregue |
| **Agora** | **Testes de `tasks/media.py`, visão de imagem, avaliação pós-atendimento** | em aberto (§3.3) |
| Depois | Distribuição automática, merge de contatos, campanhas | Crescimento |

**Nota de implementação:** toda dependência da Evolution segue isolada atrás do adapter de canais.
A migração à Cloud API saiu do roadmap (§2.2), mas o isolamento **não** foi relaxado: ele é o que
mantém barata a saída caso a conexão não-oficial se torne inviável.

**Risco nº 1 (resolvido para o LLM):** o agente já rodou contra provedor real (Gemini), e o guia de
conteúdo em `docs/guias/GUIA-CONTEUDO-AGENTE.md` foi escrito a partir dessas execuções, não da documentação.
A transcrição de áudio herda a mesma chave quando o provedor sabe transcrever, e áudio que não vira
texto deixou de ser silêncio: o cliente é avisado e, na segunda vez, a equipe é chamada.

**Risco aberto nº 1:** a entrega depende de conexão não-oficial. O `@lid` do WhatsApp não é
endereço entregável — envio para ele volta `ERROR` do próprio Evolution — e é por isso que
`channels/whatsapp.py::_destino` manda no telefone (`senderPn`) quando o endereço é um lid.
Contato antigo criado sem `senderPn` não tem telefone e o envio **falha de propósito**, virando
fila da equipe com nota, em vez de fingir entrega.

**Não é mais risco:** a ausência de caminho de deploy. Há `render.yaml` e roteiro em
`docs/DEPLOY.md`; executá-lo é decisão de quem vai a produção (§3.2).

---

## 9. Estrutura de código (estado atual)

```
app/
├── models/    core (Tenant, ChannelConnection, Contact, Conversation, Message, …) · enums
│              salao · contatos · fluxo · audit
├── channels/  types · base (ChannelProvider) · whatsapp (EvolutionProvider) · factory
│              audited (auditoria de efeito externo)
├── flow/      schema (validador) · engine (decidir) · registro · texto · horario
├── ai/        llm · embeddings · retrieval · chunking · transcription
├── api/       auth · inbox · webhooks · ws · channels · bot · fluxo · media · reports ·
│              team · quick_replies · contatos · salao · audit
├── services/  assignment (captura atômica) · ingestion · conversation_state · inbox_queries
│              bot_orchestrator · bot_settings · ai_context · copilot · kb · contact_memory
│              flow_store · midia · attachments · media_links · storage · avatars · audit
│              salao_agenda · salao_acoes · salao_contexto · salao_fluxo · salao_fotos
│              salao_resumo · salao_aviso · contatos · reports · team · …
├── tasks/     ingest · bot · media · runner
└── core/      config · database · ratelimit
evals/         avaliação offline do agente (casos, checagens, relatório)
```

> Esta árvore é resumo, não inventário: o diretório é a fonte. A **fronteira** entre o que é
> núcleo estável e o que foi desenhado para ser trocado está em `docs/produto/PLATAFORMA.md` §1.

---

## 10. Plano de execução do MVP

> **Registro histórico.** Esta seção é o plano como foi traçado e cumprido — **não é o estado
> atual**, que está em §3.1 (o que foi entregue) e §9 (a estrutura). Mantida porque a ordem das
> fases explica decisões que o código já não mostra sozinho.
>
> Onde ela descreve telas (Fase 4), é uma era anterior à camada de tokens e ao tema escuro. O
> front atual está descrito em `frontend/CLAUDE.md`.

Ordem obrigatória: cada fase depende da anterior. Não avançar para a próxima sem a atual funcionar e estar comitada.

---

### Fase 1 — Fundação

Objetivo: ambiente rodando localmente, banco com schema real, sem nenhuma feature de produto ainda.

**Entregas:**
- `app/main.py` — instância FastAPI com lifespan, CORS e rotas base
- `app/core/config.py` — settings via `pydantic-settings` (lê do `.env`)
- `app/core/database.py` — `AsyncSession` factory com SQLAlchemy async
- `app/worker.py` — instância Celery apontando para Redis
- `docker-compose.yml` — serviços: PostgreSQL, Redis, Evolution API
- `.env.example` — todas as variáveis necessárias documentadas
- Migration inicial do Alembic — gera e aplica o schema completo do `models/core.py` (todas as tabelas: `tenants`, `channel_connections`, `contacts`, `conversations`, `messages`, `conversation_events`)
- Atualizar comandos do `CLAUDE.md` para refletir o que realmente roda

**Critério de conclusão:** `docker compose up -d` + `alembic upgrade head` + `uvicorn app.main:app --reload` sobem sem erro.

---

### Fase 2 — Ingestão

Objetivo: uma mensagem enviada no WhatsApp aparecer gravada no banco.

**Entregas:**
- `app/services/ingestion.py` — recebe `NormalizedInbound`, acha-ou-cria `Contact` e `Conversation`, grava `Message` respeitando o `UniqueConstraint(channel, external_id)` (idempotência), atualiza `last_message_at` / `last_message_preview` / `unread_count` na conversa
- `app/api/webhooks.py` — endpoint POST `/webhooks/{channel}/{webhook_id}` que chama `verify_webhook` e despacha para o worker
- `app/tasks/ingest.py` — task Celery que chama `ingestion.py`
- Redis pub/sub — ao gravar mensagem, publica evento `{tenant_id}:{conversation_id}` no canal Redis
- `app/api/ws.py` — endpoint WebSocket `/ws/{tenant_id}` que assina o canal Redis e empurra eventos para o frontend

**Critério de conclusão:** mensagem enviada no WhatsApp → webhook recebido → mensagem no banco → evento chegando no WebSocket.

**Regras a respeitar:**
- `verify_webhook` sempre antes de processar — rejeitar payload inválido com 401
- Evento duplicado (mesmo `external_id`) é no-op, nunca erro 500
- Nenhum import de Evolution fora de `channels/whatsapp.py`

---

### Fase 3 — API da Inbox

Objetivo: endpoints REST completos para a inbox funcionar.

**Entregas:**
- `app/api/conversations.py`:
  - `GET /conversations` — fila geral (não atribuídas)
  - `GET /conversations/mine` — minhas conversas
  - `GET /conversations/all` — visão de gestor
  - `GET /conversations/{id}/messages` — histórico de mensagens
  - `POST /conversations/{id}/claim` — capturar (UPDATE atômico)
  - `POST /conversations/{id}/release` — liberar
  - `POST /conversations/{id}/transfer` — transferir para outro atendente
  - `POST /conversations/{id}/messages` — enviar mensagem (chama `provider.send`)
  - `PATCH /conversations/{id}/status` — marcar como resolvida / reabrir
- `app/api/auth.py` — autenticação JWT com `tenant_id` no payload
- Schema OpenAPI completo e exportável (FastAPI gera automaticamente em `/openapi.json`)

**Critério de conclusão:** todos os endpoints respondem corretamente, captura atômica validada (duas requisições simultâneas → apenas uma captura).

**Regras a respeitar:**
- Captura/liberação/transferência sempre via `services/assignment.py` — nunca reimplementar a lógica
- `tenant_id` obrigatório em todas as queries
- Erro de "já capturada" retorna 409 com `current_owner_id` no body

---

### Fase 4 — Frontend (inbox) ✓ concluída

Objetivo: interface funcional da inbox em Next.js, consumindo a API e o WebSocket.

**Entregas concluídas:**
- Setup Next.js + Tailwind + cliente TypeScript escrito à mão
- Layout: top navbar horizontal + sidebar contextual clara + área principal
  - `Navbar` — logo, tabs (Inbox / Contatos / Relatórios / Configurações), busca, avatar
  - `Sidebar` — muda conforme a seção: "Caixas + Canais" na inbox; "Canais + Conta" em Configurações
- Página `/inbox`:
  - `InboxView` — orquestra lista + thread, conecta ao WebSocket
  - `ConversationList` — três filas (Tudo / Não atribuídas / Minhas), avatar, preview, horário, badge
  - `ConversationThread` — balões de mensagem (recebida/enviada), scroll automático
  - `ThreadHeader` — Assumir / Liberar / "Em atendimento", banner de posse, erro de conflito 409
  - `ReplyInput` — campo de texto + envio, bloqueado quando conversa não é do atendente
  - Atualização em tempo real via WebSocket
- Página `/channels` (Configurações → WhatsApp):
  - Fluxo de conexão inline em 3 estados: QR aguardando → confirmando → conectado
  - Lista de conexões ativas com "Ver status" e "Remover"
  - Nota de aviso sobre limite de aparelhos do WhatsApp
  - Breadcrumb: Configurações › Canais › WhatsApp

**Critério de conclusão:** atendido — mensagem chega no WhatsApp, aparece na inbox em tempo real, atendente captura, responde, conversa vai para "Minhas".

---

### Fase 5 — Canais adicionais ✗ cancelada (2026-07-25)

Instagram DM e e-mail chegaram a ser implementados e foram **removidos** em 2026-07-24
(`refactor - remove canais instagram e email foca em whatsapp`). O foco é 100% WhatsApp.

Motivo: o Instagram depende de revisão de app da Meta (prazo fora do nosso controle) e o e-mail
puxava IMAP/SMTP + Celery beat para manter um canal que o público-alvo mal usa. Ambos custavam
manutenção antes de existir cliente pedindo.

Se um dia voltar: `ingestion.py` e `ChannelProvider` continuam agnósticos de canal — re-adicionar é
escrever o provider e registrá-lo em `channels/factory.py`, sem migration nem mudança no núcleo.
É exatamente o que a regra §1 do `CLAUDE.md` protege.
