# PLATAFORMA.md — construindo um sistema nichado sobre o Otto

Este documento responde uma pergunta que os outros não respondem:

> **Isso que eu quero fazer cabe em configuração, ou vai me custar um patch a cada atualização?**

| Documento | Pergunta |
|---|---|
| `README.md` | o que é isto e como eu rodo |
| `docs/produto/OTTO_PROJETO.md` | o que o produto é e por quê |
| `CLAUDE.md` / `frontend/CLAUDE.md` | posso fazer isso **neste** repositório? |
| **este** | **como construir OUTRO repositório a partir deste** |

Aqui não há arquitetura nem regra repetida — só ponteiro. Se algo parecer faltando, está num dos
quatro acima.

---

## 0. O modelo

**Cada sistema nichado é um fork deste repositório**, com seu deploy, sua marca e sua persona. Não é
um tenant a mais no mesmo servidor.

**Implantar é decisão do derivado.** O repositório traz CI e um `render.yaml` de referência
(`docs/DEPLOY.md`), mas onde e como hospedar depende de onde cada derivado vai rodar — essa
decisão não é da base.

Uma consequência que economiza tempo: **o multi-tenant continua existindo dentro do seu fork.** Um
derivado atende N clientes do mesmo nicho como N tenants. O fork separa *nichos*, não *clientes*.

---

## 1. A fronteira

Tudo neste documento depende desta classificação. Ela não é sobre qualidade de código — é sobre
**quanto cada arquivo vai doer em toda atualização que você puxar da base**.

### Núcleo — não mexa

`app/models/core.py` · `app/models/enums.py` · `app/services/assignment.py` ·
`app/services/ingestion.py` · `app/services/inbox_queries.py` · `app/channels/base.py` ·
`app/channels/types.py` · `app/core/` · migrations existentes em `alembic/versions/`

Editar aqui é conflito garantido em todo merge, para sempre. Se você precisou, o mais provável é que
**falte uma costura** — considere pedir a mudança na base em vez de manter um patch local.

### Costura — desenhado para ser trocado

| Onde | O que troca |
|---|---|
| `app/channels/factory.py::_registry` | qual provider atende cada canal |
| `app/ai/llm.py::_OPENAI_COMPAT_BASE` e `::_DEFAULT_MODEL` | provedores de LLM |
| `app/ai/embeddings.py::_MAX_DISTANCE` | corte de relevância do RAG por provedor |
| tabela `bot_settings` | persona, conhecimento, provider, modelo, chave, mensagem de transferência, **modo (agente/fluxo) e horário** |
| tabela `bot_flows` + `conteudo/fluxo.json` | o roteiro inteiro do atendimento sem IA |
| `app/flow/registro.py::registrar` | tipos de nó que o seu nicho acrescenta ao roteiro |
| **valores** em `frontend/app/globals.css` | identidade visual inteira |
| `.env` | portas, chaves, provedor de transcrição |

São edições de **uma linha em contexto estável**. Conflitam pouco e resolvem em segundos.

### Seu — crie à vontade

`app/api/<seu>.py` · `app/services/<seu>.py` · migration nova · `frontend/app/(inbox)/<rota>/page.tsx`
· `frontend/components/<Seu>.tsx` · seus próprios documentos

**Arquivo que a base não tem nunca conflita.** É o caminho preferencial para qualquer coisa que não
caiba numa costura.

### Campo minado — todo derivado mexe, e a base também

| Arquivo | Como reduzir o dano |
|---|---|
| `frontend/app/globals.css` | mexa só nos **valores** dos tokens, nunca na estrutura nem na ordem dos blocos |
| `frontend/components/Sidebar.tsx` | concentre os itens do nicho num array separado, no fim do arquivo — não intercale nos existentes |
| `frontend/types/api.ts` · `frontend/lib/api.ts` | acrescente no fim do objeto/arquivo; não reordene o que já existe |
| `app/main.py` | são 12 `include_router`; acrescente o seu no fim do bloco |
| `app/ai/llm.py::_agent_system` | é o mais caro de todos — a base mexe aqui com frequência. Ver §5 |

---

## 2. As invariantes que você herda

Cada uma existe por causa de um problema real. A regra você encontra no `CLAUDE.md`; o que está
aqui é **por que ela sobrevive no seu fork** — e é isso que some quando alguém só copia o código.

### 2.1 O núcleo nunca importa um provider de canal

*Onde:* `app/channels/base.py`, `factory.py` · *Regra:* `CLAUDE.md` §1

**Por quê:** a Evolution é conexão **não-oficial**, via aparelho pareado, sujeita a ban. O isolamento
não é preparação para uma migração — é o preço de conseguir sair barato se a conexão se tornar
inviável.

**No seu fork isso é mais forte, não menos:** quem toma o ban é o seu cliente, e você vai ser quem
precisa trocar de provider às pressas. Se você importar `EvolutionProvider` dentro de `app/api/` ou
`app/services/`, essa saída deixa de existir.

### 2.2 Captura de conversa é UPDATE condicional em SQL

*Onde:* `app/services/assignment.py` · *Regra:* `CLAUDE.md` §2

**Por quê:** ler o objeto, checar `assigned_user_id` em Python e salvar reabre o race de dois
atendentes capturando a mesma conversa. A condição vive no `WHERE`, e é o banco que arbitra.

**O que quebra:** dois atendentes respondendo o mesmo cliente, cada um sem saber do outro. Não
aparece em teste feliz — só em produção, com equipe grande e hora de pico.

### 2.3 `tenant_id` em toda query — e a rede de proteção é parcial

*Onde:* toda query de `services/` e `api/`

**A nota honesta:** existe RLS no Postgres em **uma única tabela**, `kb_documents` (migration
`b3f1c9a2d4e5`, com o GUC `app.tenant_id` setado em `app/ai/retrieval.py` e `app/services/kb.py`).
Todas as outras dependem 100% da disciplina de incluir `tenant_id` no `WHERE`.

**Consequência direta para você:** **toda tabela que o seu derivado criar herda a disciplina, não a
rede.** Se o seu nicho exige isolamento garantido no banco, a migration de RLS é sua para escrever.

### 2.4 Idempotência do webhook

*Onde:* `UniqueConstraint(channel, external_id)` em `app/models/core.py`

**Por quê:** o Evolution reentrega evento. Duplicata precisa ser **no-op**, não erro — tratar como
erro enche o log e mascara falha de verdade.

### 2.5 Cadência e anti-flood

*Onde:* `bot_orchestrator._is_stale` · `BOT_REPLY_MAX_AGE_SECONDS` · `TYPING_MS_PER_CHAR` ·
`scripts/otto.sh`

**Por quê, e são três incidentes distintos:**
1. O WhatsApp **entrega o backlog ao reconectar**. Sem a guarda de idade, o bot respondeu a todo
   mundo que escreveu enquanto ele estava fora — inclusive quem já tinha sido atendido.
2. Bolhas em rajada denunciam robô, e numa conexão não-oficial isso é risco de ban. Daí o atraso
   proporcional ao tamanho do texto, com indicador de "digitando".
3. Worker parado acumula fila; ao subir, drena tudo de uma vez. Por isso `otto.sh restart` purga.

**Se você mexer no orquestrador e perder qualquer uma das três, quem recebe a mensagem indevida é
um cliente real.**

### 2.6 Cor por token, e o gate que a defende

*Onde:* `frontend/app/globals.css` · *Regra:* `frontend/CLAUDE.md` §1-2

**O que o fork precisa saber além da regra:** `npm run check:contrast` **lê os valores do próprio
`globals.css`** e verifica 40 pares nos dois temas. Trocar a paleta do nicho e não rodar o gate joga
fora a acessibilidade que já custou quatro correções — inclusive uma borda que estava em 1,13:1 e
um verde que reprovava sob texto branco.

---

## 3. Receitas

### Canal novo

1. `app/channels/<canal>.py` — **arquivo novo**, implementando os três abstratos de `ChannelProvider`
   (`verify_webhook`, `parse_webhook`, `send`) e os opcionais que fizerem sentido (`provision`,
   `get_status`, `deprovision`, `fetch_media`, que têm default no-op).
2. Um valor em `Channel` (`app/models/enums.py`).
3. **Uma linha** em `factory.py::_registry`.

Campo específico do canal vai em `channel_metadata` (JSONB) — **adicionar canal não gera migration
de schema**. Custo de merge: duas linhas em contexto estável.

### Provedor de LLM novo

Se for compatível com a API da OpenAI: **uma linha** em `_OPENAI_COMPAT_BASE` e outra em
`_DEFAULT_MODEL`. `PROVIDERS` deriva sozinho, e `app/api/bot.py` já valida contra ele.
Se não for: um `_call_<x>` novo e um ramo em `_complete`.

**Não esqueça o front — ele é manual.** `PROVIDERS` em `frontend/app/(inbox)/agente/page.tsx` e o
union `BotProvider` em `frontend/types/api.ts` são escritos à mão; não há codegen do OpenAPI neste
projeto. Provider que só existe no backend não aparece no painel.

### Tela nova

`frontend/app/(inbox)/<rota>/page.tsx` (novo) + método em `lib/api.ts` + tipo em `types/api.ts` +
entrada em `Sidebar.tsx`. **Três registros de navegação**, e esquecer qualquer um falha em silêncio —
`SETTINGS_PATHS`, `settingsNav` e o `TITLES` da `TopBar`. Use os primitivos de `components/ui/` antes
de escrever markup. `npm run check` antes de commitar.

### Campo de negócio novo

Dois caminhos, e a escolha decide o custo:

**Sem código — serve para a maioria dos nichos.** O que a IA coleta (`decision["captured"]`) é um
dicionário **arbitrário, decidido pelo modelo a partir da persona**. Ele cai em
`bot_sessions.variables` e `contacts.profile`, ambos JSONB. Um petshop que precisa de "espécie do
pet" e um buffet que precisa de "número de convidados" **não têm uma linha de código diferente** —
têm personas diferentes. Nada de migration, nada de patch.

**Com código — quando o campo precisa ser validado ou tipado.** Hoje não há onde encaixar:
`llm._normalize_decision` faz `{str(k): str(v)}` e pronto. Data, número, CPF — tudo vira string
livre. Se o seu nicho depende de validação real, é patch no núcleo, e você vai carregá-lo em todo
merge. Dito de frente para você decidir com o custo à vista.

### Identidade visual

**Só os valores** em `frontend/app/globals.css`. Nunca hex em componente, nunca `dark:` — o gate
reprova, e `dark:` é sinal de que falta um token. Depois: `npm run check:contrast`, obrigatório.
Nome e metadados ficam em `frontend/app/layout.tsx` e na tela de login.

Mantenha isso num **commit único e isolado** (`chore - marca do derivado`). Depois de um merge
sujo, é o que permite revisar o que é seu em um diff só.

### Conta nova

`python3 scripts/criar_conta.py --nome "..." --email "..."` — único caminho; não há rota HTTP que
crie tenant. O convite por link em *Configurações › Equipe* só faz crescer conta existente.

---

## 4. Puxar correções da base

```bash
git remote add upstream <url-do-otto>
git fetch upstream
git merge upstream/main
```

**Merge, não rebase.** Rebase reescreve a história do seu fork e faz você **resolver o mesmo conflito
uma vez por commit seu**. Merge resolve uma vez, registra o resultado, e o merge seguinte parte dali.

### A regra que decide tudo

> **Prefira criar um arquivo novo a editar um arquivo do núcleo.**

Num repositório comum isso é questão de gosto. Num fork é aritmética:

- arquivo que só existe no seu derivado **nunca conflita — nunca, para sempre**;
- cada linha que você altera num arquivo que a base também altera é um conflito que **volta em toda
  atualização**, não uma vez.

Por isso registro-por-linha (`_registry`, `_OPENAI_COMPAT_BASE`) é barato: o contexto ao redor é
estável. E por isso reescrever um bloco de `_agent_system` é caro: é justamente onde a base mexe.

Depois de todo merge: `pytest` · `cd frontend && npm run check` · `alembic upgrade head`.

---

## 5. O que hoje obriga a mexer no núcleo

A lista honesta. É o que decide se o seu nicho cabe em configuração.

| Item | Onde | Veredito |
|---|---|---|
| Contrato JSON do agente (`reply`, `captured`, `status`, `summary`, `priority`, `sentiment`), regras de bolha e emoji, protocolo de handoff | `app/ai/llm.py::_agent_system` | **patch** — e é o arquivo mais caro de manter divergente |
| Strings em português cravadas, sem i18n | `WAIT_MESSAGE`/`MEDIA_MESSAGE` (`bot_orchestrator.py`), `_RAG_SYSTEM` (`app/api/bot.py`), `DEFAULT_TRANSFER_MESSAGE` (`bot_settings.py`), front inteiro | **patch**, se o nicho não for pt-BR |
| `captured` sem schema nem validação | `llm._normalize_decision` | **patch**, se precisar validar |
| Transcrição é por deployment (`.env`), não por tenant | `app/ai/transcription.py` | **patch**, se variar por cliente |
| Embeddings só openai e gemini | `app/ai/embeddings.py` | costura estreita — uma linha, se o provedor devolver 1536 dims |
| Só WhatsApp registrado | `channels/factory.py` | **costura** — ver receita em §3 |
| Tipos de nó do roteiro | `app/flow/registro.py` | **costura** — `registrar()` por tipo, como a factory de canais |
| RLS só em `kb_documents` | migration `b3f1c9a2d4e5` | disciplina, não configuração — ver §2.3 |
| Provisionamento só por script | `scripts/criar_conta.py` | **patch**, se o nicho quer cadastro self-service |

---

## 6. Checklist do primeiro dia

- [ ] Fork do repositório e `git remote add upstream <url-do-otto>`
- [ ] `cp .env.example .env`
- [ ] **`SECRET_KEY` nova** — é dela que se deriva o Fernet que cifra `bot_settings.api_key`
      (`app/services/bot_settings.py::_fernet`). Reaproveitar a da base significa que qualquer um com
      a chave dela lê as chaves de IA dos seus clientes
- [ ] `WEBHOOK_BASE_URL` preenchido — vazio, o QR conecta e nenhuma mensagem chega (ver README)
- [ ] `pip install -r requirements.txt --break-system-packages` e `npm install` em `frontend/`
- [ ] `alembic upgrade head`
- [ ] `python3 scripts/criar_conta.py --nome "..." --email "..."`
- [ ] Identidade visual nos valores de `globals.css` + `npm run check:contrast`
- [ ] Persona, conhecimento e documentos do nicho em `conteudo/`, seguidos de
      `python3 scripts/sincronizar_conteudo.py --conta "..."` (ver `docs/guias/GUIA-CONTEUDO-AGENTE.md`)
- [ ] `pytest` e `cd frontend && npm run check` verdes
- [ ] Commit único de marca, isolado do resto

---

## 7. Como manter este documento

Toda afirmação aqui é **ponteiro para um caminho**, nunca cópia de conteúdo. Foi copiar conteúdo que
apodreceu a §10 do `docs/produto/OTTO_PROJETO.md` e matou o antigo `DESIGN.md`, que passou a ensinar exatamente o
que o gate de tokens reprova.

Mexeu em `channels/factory.py`, em `_OPENAI_COMPAT_BASE`, na interface `ChannelProvider`, na
estrutura de `globals.css` ou nos limites de `bot_settings`? **Este arquivo está no raio** — releia
a §1 e a §5.
