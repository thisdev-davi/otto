# CLAUDE.md — Otto

Guia operacional para trabalhar neste repositório. Leia antes de gerar ou alterar código.
Para visão de produto e arquitetura, ver `docs/produto/OTTO_PROJETO.md`.
Para construir um sistema nichado **a partir** deste repositório, ver `docs/produto/PLATAFORMA.md`.

---

## O que é o Otto

Inbox de atendimento de **WhatsApp** (Evolution API/baileys) para pequenos negócios, com agente de IA.
Backend FastAPI + Celery, PostgreSQL, Redis; frontend Next.js 15.5. Multi-tenant por linha.

WhatsApp é o único canal ativo: Instagram e e-mail foram removidos por decisão (`docs/produto/OTTO_PROJETO.md` §3.2)
e a migração para a Cloud API saiu do roadmap (§2.2). A regra 1 abaixo continua valendo mesmo assim —
ver o porquê nela.

---

## Regras invioláveis

Estas duas regras previnem bugs já mapeados na arquitetura. Não as contorne.

### 1. Isolamento do adapter de canais
- O núcleo (`models/`, `services/`, rotas) **nunca** importa um provider de canal concreto.
- Toda interação com um canal passa pela interface `ChannelProvider` (`channels/base.py`), obtida via `channels/factory.py::get_provider()`.
- **Nenhum import de Evolution fora de `channels/`.** O código específico de WhatsApp mora só em `channels/whatsapp.py`.
- **Por que a regra sobrevive ao cancelamento da migração:** com um único provider ativo, ela deixa de parecer necessária — e é aí que se perde. A Evolution é conexão não-oficial, sujeita a ban; trocar de provider (Cloud API ou outro) deve continuar sendo trocar a classe registrada para `Channel.WHATSAPP` em `factory.py`, e nada mais. O isolamento é o preço da saída barata, não do plano de migração.

### 2. Captura de conversa é sempre atômica
- Capturar, liberar e transferir conversa usam **UPDATE condicional em SQL** (ver `services/assignment.py`). **Nunca** ler o objeto, checar `assigned_user_id` em Python e depois salvar — isso reabre o race condition de dois atendentes capturando a mesma conversa.
- A condição vai no `WHERE`: captura exige `assigned_user_id IS NULL`; liberação e transferência exigem que o solicitante seja o dono atual.
- `tenant_id` entra em **toda** query de conversa/mensagem (defesa em profundidade multi-tenant).
- Mudança de estado da conversa e o `ConversationEvent` correspondente vão na **mesma transação**.

### 3. Reserva de data é sempre atômica (derivado salão)

Mesma classe de erro da regra 2, em outro lugar do sistema.

- A exclusividade de uma data é do **índice parcial único** `uq_salao_reservas_data_ativa`
  (`tenant_id, data` WHERE `status IN ('pre_reservada','confirmada')`), e a escrita é um
  `INSERT ... ON CONFLICT DO NOTHING` (`services/salao_agenda.py::criar_reserva`).
- **Nunca** ler as reservas, concluir em Python que a data está livre e depois inserir.
  A consulta que existe antes do INSERT serve só para dizer o motivo da recusa ao usuário —
  ela não autoriza a escrita. Zero linhas inseridas = outra transação ganhou a corrida.
- O índice é **parcial** de propósito: reserva cancelada ou expirada fica no banco como
  histórico sem segurar a data. Trocar por unique constraint cheia quebra o cancelamento.
- Pré-reserva vencida é expirada **preguiçosamente**, na leitura e na disputa da data
  (`expirar_vencidas`). Não há `celery beat` no projeto — não introduza um só para isto.
- O agente **nunca** confirma reserva: ele cria `pre_reservada` com prazo, e quem confirma
  é humano. Preço e disponibilidade vêm da agenda, nunca do texto de conhecimento.

### 4. Disponibilidade tem uma implementação só

- **O cadastro é por data.** Uma regra por dia da semana chegou a existir e foi retirada
  por decisão de produto: neste nicho o preço é ditado por feriado e data comemorativa,
  que são conjuntos irregulares. Quem preenche muitas datas usa a seleção múltipla do
  calendário ou `definir_em_lote`. **Não reintroduza padrão semanal sem pedido.**
- Quem decide se uma data está à venda é `salao_agenda.resolver`, e ela é a **única**
  fonte de verdade. O front lê o `motivo`/`livre` que vêm prontos
  (`lib/salao.ts::estadoDoDia`); deduzir "livre" de `preco !== null` no cliente cria uma
  segunda implementação da regra, e a divergência aparece como preço errado dito ao
  cliente.
- `listar` devolve o intervalo INTEIRO, cadastrado ou não, para o calendário não precisar
  inventar as lacunas — inventar lacuna é onde a segunda implementação nasce.
- Motivo novo de recusa entra em `MOTIVO_*`, em `_ROTULO_MOTIVO` (o que o agente lê) e na
  união `MotivoData` do TS. Rótulo que o agente recebe e a persona não explica vira
  improviso do modelo.

### 5. Tenant nunca vem do corpo da requisição

Vale para toda entrada não autenticada por JWT — hoje, o webhook de canal.

- O tenant de um evento inbound sai do **`webhook_id` secreto no caminho da URL**,
  resolvido em `services/channels.py::resolve_by_webhook_id`. O campo `instance` do
  corpo é **conferido** contra a conexão resolvida; ele não escolhe nada.
- **Por quê:** era assim que o Otto resolvia o tenant, e isso significava que quem
  tivesse a chave compartilhada do Evolution escolhia em qual cliente da plataforma a
  mensagem forjada caía — inclusive fazendo o bot responder para números arbitrários.
  Ver `SEGURANCA.md` §C1.
- Segredo é **por conexão**, nunca um só para a plataforma, e a comparação é
  `hmac.compare_digest`. A rota legada `POST /webhooks/{channel}`, que resolvia o tenant pelo
  corpo, foi removida junto com esse caminho em `ingestion.ingest`: `connection_id` é
  obrigatório. Não reintroduza um caminho de ingestão sem conexão autenticada.

### 6. Mimetype guardado não é confiável

- Arquivo recebido pelo canal traz o mimetype que **o remetente escolheu**. Servir isso
  de volta cru deixava um estranho entregar HTML que executava na origem da API, com o
  token do atendente na própria URL (`SEGURANCA.md` §C2).
- Quem decide o `Content-Type` de resposta é `attachments.mimetype_para_servir()`, contra
  a allow-list `_ACEITOS` e o `ContentType` gravado. Fora dela: `application/octet-stream`
  com `Content-Disposition: attachment`.
- **URL de mídia não carrega JWT.** O que vai na query é assinatura de curta duração
  (`services/media_links.py`), válida para um recurso só. Não reintroduza `?token=`.


### 7. O fluxo é o segundo cérebro, não um segundo sistema

Atendimento por fluxo (roteiro determinístico, sem IA) é o **nível 1** do produto. Ele existe
porque boa parte da carteira não quer nem precisa de LLM — quer previsível e barato.

- **Mesmo contrato de decisão.** `app/flow/engine.py::decidir` devolve o MESMO dicionário que
  `app/ai/llm.py::agent_turn` (`reply`, `captured`, `status`, `summary`, `priority`,
  `sentiment`), e `bot_orchestrator._handle` escolhe entre os dois por `bot_settings.modo`.
  Tudo o que vem antes (as sete guardas) e depois (bolhas com cadência, handoff atômico, nota
  de triagem, ações do salão, persistência) é **um código só**. Foi essa escolha que evitou
  tocar em `app/ai/llm.py`. Não crie caminho de envio próprio para o fluxo.
- **Avançar de nó é transição DISPUTADA.** Não há debounce no projeto: cada mensagem dispara
  seu turno, e ainda existe a re-tentativa agendada. Ler `variables.fluxo.no`, decidir em
  Python e salvar é a corrida das regras 2 e 3 num campo que ninguém suspeita — o sintoma é o
  cliente recebendo o menu duas vezes ou pulando uma pergunta. Quem arbitra é o UPDATE
  condicional de `flow_store.avancar` (`WHERE variables#>>'{fluxo,turno}' = :turno_lido`), e
  quem perde a corrida **não envia nada**.
- **Preço e disponibilidade nunca são texto do roteiro.** Só o nó `agenda` os produz, e só de
  `salao_agenda.resolver`. O validador reprova `R$`/"reais" em qualquer texto — mesma trava do
  conteúdo do agente, mesmo motivo. As sete saídas do nó são **obrigatórias**: motivo
  sem ramo é o caminho por onde um caso cai onde fala valor.
- **`sem_cadastro` responde; `sem_preco` e `agenda_vazia` chamam gente.** Data fora da agenda é
  a pergunta mais comum de um salão que só vende fim de semana — encaminhar todas entope a fila
  com o que o roteiro sabe resolver, então ele diz que a data não está aberta e oferece
  `{{proximas_livres}}`. Duas exceções, e são elas que impedem a resposta automática de virar
  mentira: dia cadastrado SEM valor (`sem_preco`) contradiria o próprio cadastro, e agenda sem
  NENHUMA data futura à venda (`agenda_vazia`, produzido por `salao_fluxo`, não pelo service)
  é o estado de todo cliente novo — ali o bot despacharia sozinho todo mundo que pergunta por
  data, oferecendo "nenhuma".
- **Variável sem valor não vira string vazia.** "O valor dessa data é ." é pior que erro.
  `flow/texto.py::renderizar` devolve o que faltou e o motor trata isso como roteiro quebrado:
  encaminha com nota interna. Substituição é **literal, nunca `str.format`** (regra da
  `render_confirmacao`, mesmo risco).
- **Roteiro com defeito degrada para gente.** Nó que sumiu, saída inexistente, ciclo não pego
  pelo validador: todos terminam em fila da equipe com nota dizendo o que quebrou. Nunca
  exceção, nunca silêncio.
- **Nó novo do nicho entra por `flow/registro.py::registrar`**, não por patch no motor — mesmo
  padrão de `channels/factory.py::_registry`. Quem registra `agenda` e `acao` é
  `services/salao_fluxo.py`.
- **O fluxo não consome o teto diário de LLM**: aquele teto é de gasto com modelo, e o roteiro
  não chama nenhum.
- **O painel edita TEXTO, nunca ESTRUTURA.** `PATCH /fluxo/textos` recebe só o que mudou e
  aplica sobre o grafo publicado **no servidor** (`schema.aplicar_textos`). Não é limitação de
  UI: é o que faz a edição pelo cliente ser segura, porque não existe requisição capaz de criar
  saída solta, apagar um ramo de `MOTIVO_*` ou trocar o tipo de um nó. Aceitar um grafo inteiro
  vindo do navegador devolveria essa classe inteira de defeito. Quem muda o desenho é
  `conteudo/fluxo.json`.
- **O simulador não escreve.** `engine.simular` roda o roteiro em memória: sem `flow_store.avancar`,
  sem nota interna, sem envio. Os executores que gravam são desviados por `Contexto.simulacao` —
  quem testa o roteiro no painel não pode segurar a data de um cliente real. A agenda continua
  sendo **lida**, e isso é o ponto: ver o preço verdadeiro é o motivo de o simulador existir.
- **Fluxo assumido no painel não é sobrescrito em silêncio.** `flow_store.assumir_no_painel`
  desliga `gerenciado_por_arquivo`, e a partir daí `sincronizar_conteudo.py` recusa publicar sem
  `--forcar`. Mesma proteção que `conteudo_gerenciado` dá à persona, no sentido contrário.
- **Mídia é turno do roteiro, não caso especial fora dele.** Foto, vídeo, documento e figurinha
  entram por `engine.decidir(..., midia=...)` e contam no MESMO `invalidas` do texto sem sentido:
  a primeira avisa e repete o nó, a segunda cai em `config.no_handoff`. Tratá-las antes dos dois
  cérebros era o desenho antigo e produzia o pior desfecho possível — uma frase fixa uma vez por
  conversa e, da segunda em diante, silêncio total, sem nota e sem encaminhar. O texto por tipo
  mora em `services/midia.py`, um só para os dois cérebros: o que se diz sobre uma foto não pode
  mudar quando alguém troca o modo no painel. **Áudio é a exceção** e não se responde na ingestão:
  `tasks/media.py` transcreve e reentra como texto — ou, se falhar, como `audio_ilegivel=True`.

**`bot_settings.horario` NÃO é o padrão semanal que a regra 4 proíbe.** A regra 4 é sobre qual
*data* está à venda e por quanto — ali o padrão semanal saiu porque preço de salão é ditado por
feriado e data comemorativa. Expediente é *quando existe gente para responder*: outra pergunta,
outra coluna, zero sobreposição.

---

## Outras convenções

- **Escalação avisa a equipe por evento próprio.** `events.CONVERSATION_HANDOFF` é publicado
  depois do commit, e só por quem ganhou `_claim_handoff` — a mesma trava que impede a nota de
  triagem duplicada impede o alerta duplicado. Não o troque por um campo em `message.outbound`:
  escalação também acontece em turno que não envia nada (falha de envio, IA fora do ar, teto de
  gasto), e era exatamente aí que a conversa que mais precisava de gente era a mais silenciosa
  do sistema. É o único evento além de `message.inbound` que o front transforma em aviso.
- **Foto de perfil do contato é ARQUIVO nosso, não URL do provider.** O Evolution devolve
  um endereço do CDN do WhatsApp: expira em horas e, enquanto vale, é uma URL pública que
  identifica o cliente. `services/avatars.py` baixa uma vez, guarda em
  `MEDIA_DIR/{tenant}/avatars/` e serve por link assinado, como qualquer mídia (regra 6);
  o `content-type` que volta da rede passa pela allow-list de `channels/whatsapp.py::
  fetch_avatar` antes de virar arquivo. Mora em `contacts.channel_metadata` e **nunca em
  `contacts.profile`** — aquele JSONB é reescrito inteiro a cada turno do bot e o avatar
  sumiria sem erro. A (re)busca é **preguiçosa**, decidida na ingestão e enfileirada em
  `tasks/ingest.py`: sem `celery beat`, como todo o resto do projeto.
- **Idempotência de mensagens:** toda `Message` gravada respeita `UniqueConstraint(channel, external_id)`. Ingestão de webhook deve tratar evento duplicado como no-op, não como erro.
- **Campos específicos de canal:** vão em `channel_metadata` (JSONB), nunca como coluna nova. Adicionar canal não deve gerar migration de schema.
- **Credenciais de canal:** vivem em `ChannelConnection.credentials` (JSONB). Nunca logar esse campo.
- **Reconectar o WhatsApp deixa conversas órfãs.** A FK `conversations.channel_connection_id` é
  `ON DELETE SET NULL`, e reconectar apaga a conexão antiga — toda conversa que já existia fica com
  a coluna nula. Resolver a conexão com `JOIN` nessa coluna produzia mudez total: o bot desistia sem
  registrar motivo e o atendente levava 404 ao responder. Use `channels.JOIN_CONEXAO` (LEFT JOIN
  com plano B na conexão ativa do tenant) em toda query de envio; `create_connection` religa as
  órfãs. **Não volte ao `JOIN` direto.**
- **Conversa parada é encerrada na LEITURA, nunca por agendador.** Depois de
  `CONVERSA_INATIVA_SEGUNDOS` (1 h por padrão) sem mensagem nenhuma, a próxima mensagem do
  cliente encerra a conversa antiga e abre uma nova — o roteiro recomeça do começo em vez de
  continuar parado na pergunta em que ele sumiu (`ingestion._encerrar_se_inativa`). Não há
  `celery beat` e não se cria um para isto; é a mesma escolha preguiçosa de
  `salao_agenda.expirar_vencidas`. As três condições vão no **WHERE** do UPDATE, não em `if`
  no Python: conversa **capturada** por atendente e conversa já **encaminhada à equipe**
  (`bot_sessions.handoff`) nunca são encerradas — sumir com uma delas tiraria da fila um
  cliente que está esperando, que é o oposto do que a regra existe para fazer.
- **`SENT` não é prova de entrega — `messages.update` é.** Gravar `SENT` no HTTP 201 do Evolution
  diz apenas que ele aceitou a mensagem; quem sabe se ela chegou é o ACK que volta depois, e é ele
  que também traz o `ERROR`. `_parse_status` lia `data["key"]["id"]`, mas o payload de
  `messages.update` do Evolution é **plano** (`data["keyId"]`) — todo ACK era descartado em
  silêncio e nenhuma mensagem jamais saía de `SENT`, nem as que o WhatsApp recusou. O front já
  desenha ✓ / ✓✓ / lido / falhou; ele só nunca tinha recebido o dado. **Ao mexer no adapter,
  confira o payload contra o que o provedor manda de verdade, não contra o que os testes assumem.**
- **`contacts.profile` é do agente, não da equipe.** Aquele JSONB é reescrito INTEIRO a cada turno
  do bot (`contact_memory.remember` faz SELECT, merge em Python e UPDATE, sem lock). Dado que um
  humano escreve não pode morar lá: gravado durante uma conversa ativa, ele some sem erro nenhum —
  a mesma corrida das regras 2 e 3, num campo que ninguém suspeita. Anotação da equipe é linha
  própria em `contact_notes`. **Não acrescente chave de escrita humana ao `profile`.**
- **SQL puro vs ORM:** ORM (SQLAlchemy) é o padrão; SQL explícito só onde concorrência exige (caso da captura). Não "simplificar" a captura para ORM.
- **Async:** backend é async ponta a ponta (FastAPI async, SQLAlchemy AsyncSession, httpx).
- **Tipagem:** type hints obrigatórios em código novo. O cliente TS do front (`frontend/lib/api.ts`, `frontend/types/api.ts`) é **escrito à mão** — não há codegen. Mudou schema Pydantic, atualize o tipo TS na mesma leva.

---

## Commits

- Formato: `[tipo] - [descrição breve]` — ex.: `feat - adicao componentes`
- Tipos: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `style`, `perf`, `build`, `ci`.
- Descrição curta, sem comentários ou corpo estendido.
- **Nunca** referenciar Claude, Anthropic ou qualquer ferramenta de IA na mensagem. **Sem trailer de co-author.**

---

## Estrutura

```
app/
├── models/
│   ├── enums.py     Channel, ConversationStatus, MessageDirection/Status,
│   │                ContentType, ConversationEventAction
│   ├── core.py      Tenant, ChannelConnection, Contact, Conversation,
│   │                Message, ConversationEvent
│   ├── contatos.py  ContactNote — anotação da equipe sobre o cliente
│   └── salao.py     SalaoData, SalaoReserva, SalaoFoto  (derivado)
├── flow/            # o segundo cérebro: roteiro determinístico, sem LLM
│   ├── schema.py    formato do grafo + validar() — puro, roda em 3 lugares
│   ├── engine.py    decidir() — devolve a MESMA decisão que llm.agent_turn
│   ├── registro.py  Contexto + registrar() — a costura para nós do nicho
│   ├── texto.py     {{variavel}}, substituição literal
│   └── horario.py   expediente do negócio (≠ agenda; ver regra 7)
├── channels/        # único lugar que conhece canais concretos
│   ├── types.py     NormalizedInbound, OutboundMessage, SendResult
│   ├── base.py      ChannelProvider (interface abstrata)
│   ├── whatsapp.py  EvolutionProvider (ativo) + CloudAPIProvider (destino)
│   └── factory.py   get_provider() — ponto único de troca de provider
└── services/
    ├── assignment.py     claim / release / transfer (UPDATE atômico)
    ├── inbox_queries.py  filas: unclaimed / assigned_to / all
    ├── contatos.py       perfil do cliente: histórico, agendamentos e anotações
    ├── avatars.py        foto de perfil do contato: quando buscar, onde guardar, como servir
    ├── salao_agenda.py   preço por data, resolver() e reserva atômica (regras 3 e 4)
    ├── salao_resumo.py   contexto da reserva para quem decide confirmar
    ├── salao_contexto.py agenda → bloco de conhecimento do turno
    ├── salao_fotos.py    galeria do espaço + envio pelo agente
    └── salao_acoes.py    lê `_acao_*` de captured e executa
```

O derivado do salão liga-se ao agente em **dois pontos, de propósito mínimos**:
`ai_context.build_knowledge` (1 linha, cobre bot e copilot) e `bot_orchestrator._handle`
(2 linhas). `app/ai/llm.py` **não é tocado** — as ações do agente trafegam pelo `captured`,
que é dicionário livre decidido pela persona. O texto da persona vive em
`conteudo/persona.md`, versionado — ver `docs/guias/GUIA-PERSONA-SALAO.md`. O roteiro do modo fluxo
mora em `conteudo/fluxo.json`, publicado pelo mesmo sincronizador — ver `docs/guias/GUIA-FLUXO-SALAO.md`.

---

## Auditoria, arquivos e implantação

- **Alterou uma tabela com `tenant_id`:** a migration deve instalar o trigger de auditoria; veja `docs/AUDITORIA.md`. Identidade humana vem do usuário validado no banco. Nunca registre corpo de conversa, prompt, token ou chave em metadados.
- **Efeito externo:** use os adapters de canal/armazenamento existentes. Intenção auditada antecede o despacho; resultado incerto não autoriza reenvio automático. A auditoria da alteração de banco fica na mesma transação.
- **Arquivo de mídia:** use `services/storage.py`, inclusive leitura e transcrição. O locator persistido escolhe backend; não substitua `s3://` por `open()` ou presuma disco compartilhado.
- **Encerrou/reabriu conversa:** use `services/conversation_state.py`, com condição SQL, posse e evento atômico. Inatividade não é resolução comprovada pelo bot.
- **Testes de banco:** siga `docs/VALIDACAO.md`; `ENV=test`, banco com nome de teste e Redis exclusivo são obrigatórios. A suíte nunca usa o `.env` de desenvolvimento sem override.
- **Deploy/publicação:** siga `docs/DEPLOY.md`. Segredos não são arquivos versionados.

## Comandos

Setup, subida do ambiente, criação de conta e portas estão no **`README.md`** — fonte única, para
não divergirem. O que se roda ao alterar código:

```bash
# qualidade — antes de considerar qualquer mudança pronta
ruff check .
ruff format .
pytest

# migrations
alembic revision --autogenerate -m "descrição"   # revisar o SQL gerado
alembic upgrade head

# frontend (gate: tsc + tokens de cor + contraste)
cd frontend && npm run check
```

> Depois de mexer em código do backend, `scripts/otto.sh restart` — o worker não recarrega sozinho.
> Ele purga a fila de propósito; o porquê está no README.

---

## Fluxo ao implementar uma feature

1. Modelo/migration primeiro, se houver mudança de dados (`alembic revision --autogenerate`, revisar o SQL gerado).
2. Schema Pydantic (entrada/saída) — é o que tipa o cliente do front.
3. Service (lógica) — respeitando as regras invioláveis acima.
4. Rota FastAPI fina, delegando ao service.
5. Teste cobrindo o caminho feliz e, em features de captura **ou de reserva**, a corrida
   (duas capturas / duas reservas concorrentes na mesma data).

---

## Limites de escopo

Já implementados (não estão mais fora de escopo): agente de IA, RAG, copilot, relatórios, transcrição
de áudio, quick replies, **agenda com preço por data e seleção múltipla no calendário, pré-reserva
pelo agente com resumo da conversa, galeria do espaço e conteúdo do agente versionado em
`conteudo/`**.

Já implementado (saiu de fora de escopo em 2026-09-02, a pedido): **atendimento por fluxo —
roteiro determinístico sem IA, com validador, tela de visualização e killswitch de 3 estados
no painel**. O que continua fora é o **editor visual** do fluxo: hoje o roteiro é
`conteudo/fluxo.json`, versionado e publicado pelo sincronizador.

Não implementar sem ser pedido: funil de vendas, campanhas em massa,
distribuição automática de conversas, merge de contatos, e **qualquer canal que não seja WhatsApp**.
Ver `docs/produto/OTTO_PROJETO.md` §3.2 e §8.

Decidido para a agenda, e fora de escopo até que se peça: **padrão por dia da semana** (existiu e
foi retirado), horizonte de venda, modo sombra, lembretes automáticos (exigiriam `celery beat`,
processo que não existe no projeto), orçamento formatado, visita técnica agendável, múltiplos
salões, preço por período do dia, preço por número de convidados ou pacote.

**Confirmar uma reserva avisa o cliente no WhatsApp** — foi pedido, e é o único ponto em que a
agenda fala com o canal (`services/salao_aviso.py`). Nasceu com três guarda-corpos, e eles são a
feature tanto quanto o envio:

- **Nunca automático.** A rota só envia se o corpo trouxer `mensagem`, e quem confirma lê o texto
  no diálogo antes. Confirmar sem avisar continua sendo um clique.
- **Confirmar primeiro, commitar, e só então enviar.** O `UPDATE` condicional de
  `salao_agenda.confirmar` é o que impede aviso duplicado — só devolve a reserva para quem de fato
  virou o status. Não invente uma segunda trava; e não mova o envio para dentro da transação.
- **Falha de envio não desfaz a confirmação.** A data está mesmo segurada. Volta `aviso_enviado=false`
  e o atendente reenvia pela Inbox.

O texto sai de `bot_settings.confirmacao_message` (editável por tenant) e é renderizado **no
servidor**, com substituição literal de token — nunca `str.format`, que sobre texto editável é
`KeyError` e acesso a `__class__`. Cancelar e expirar **não** avisam ninguém: seriam feature nova.

## graphify

O repositório tem um grafo de conhecimento em `graphify-out/` — nós, comunidades e relações
entre arquivos, extraído do código (AST) e dos `.md` versionados.

- **Pergunta sobre o código começa no grafo.** `graphify query "<pergunta>"` devolve um subgrafo
  com arquivo e linha por uma fração do custo de varrer o repositório: ~17 mil tokens contra
  ~220 mil. Para a relação entre duas coisas, `graphify path "<A>" "<B>" --undirected` — **o
  `--undirected` é obrigatório aqui**, porque o grafo foi construído sem direção e a busca
  dirigida, que é o padrão, responde "nenhum caminho" para pares que estão ligados. Para um
  símbolo só, `graphify explain "<símbolo>"`, que traz vizinhos, chamadores e a linha.
- **O grafo localiza, não responde.** Ele leva ao bairro certo; o arquivo e a linha ainda
  precisam ser lidos antes de afirmar qualquer coisa. E a ferramenta muda o alcance: a `query`
  sobre tenant no webhook casa por vocabulário e para em `channels/` e `api/webhooks.py`, sem
  citar `resolve_by_webhook_id` — que é a resposta exata da regra 5 e que o `explain` do símbolo
  entrega de primeira, com o `receive_webhook()` que o chama.
- `graphify-out/GRAPH_REPORT.md` só para revisão ampla de arquitetura, ou quando `query`,
  `path` e `explain` não trouxerem contexto suficiente.
- **Mexeu em código: `graphify update .`** — só AST, sem LLM e sem custo de API.
  **Mexeu em `.md`: `/graphify . --update`** — a extração de documento é semântica e o `update`
  não a refaz. Como as regras invioláveis moram nos `.md`, grafo atualizado só no código
  responde sobre a arquitetura de ontem.
