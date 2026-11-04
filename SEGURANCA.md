> Laudo histórico de 30/07/2026, preservado como referência. Não é certificação nem
> auditoria atual de produção. A consolidação de 03/11/2026 acrescentou auditoria por
> conta, identidade de sessão conferida no banco, encerramento condicional e storage
> privado compartilhável. `frontend/.env.local` saiu do índice; SQL oculta parâmetros
> e seu echo é desligado por padrão; docs/OpenAPI ficam desligados fora de dev.
> Retenção de conversas/anexos/payload bruto permanece uma decisão separada.
> Resultados atuais: `docs/VALIDACAO.md`. Limites: `docs/AUDITORIA.md`.

# Laudo de segurança — Otto

Auditoria feita antes da ida a produção (VPS com nginx + HTTPS), em 30/07/2026, sobre o
estado do repositório no commit `2fa4798`.

Escopo revisado: autenticação e sessão, autorização multi-tenant, webhook de ingestão, SQL,
upload e entrega de mídia, SSRF, desserialização, logging, camada de IA (prompt, ações do
agente, RAG) e o front-end Next.js.

**Método:** leitura de código com verificação direta na fonte de cada achado crítico —
nenhum item deste laudo veio de suposição sobre como o código "deve" funcionar.

---

## Resumo

Foram encontradas **2 vulnerabilidades críticas e 7 altas**. Juntas, as duas críticas
permitiam a **um estranho sem nenhuma credencial** injetar mensagens em qualquer tenant da
plataforma e roubar a sessão de um atendente.

Todas as críticas e altas foram **corrigidas** nesta leva, com testes que travam a
regressão. As médias e baixas estão listadas com a correção recomendada.

| Severidade | Encontradas | Corrigidas |
|---|---|---|
| Crítica | 2 | 2 |
| Alta | 7 | 7 |
| Média | 9 | 3 |
| Baixa | 6 | 3 |

### O que já estava certo

Vale registrar, porque é o que tornou a auditoria rápida e é o que não se deve perder:

- **SQL 100% parametrizado.** Auditei todos os `text()` do projeto. Não há injeção. As três
  interpolações que existem (`salao_agenda`, `inbox_queries`, `team`) montam a query a
  partir de constantes privadas ou de dicionários com chave de enum — nunca de valor do
  usuário. O escape de curinga no LIKE (`inbox_queries.py`) está correto, que é o ponto que
  a maioria dos projetos erra.
- **`tenant_id` nunca vem do cliente** em nenhuma rota — sai sempre do token.
- **Papel lido do banco**, não do JWT, para que rebaixar alguém valha no request seguinte.
- **UPDATE condicional** na captura de conversa e no aceite de convite; índice parcial
  único na reserva de data. A disciplina de concorrência do projeto é boa.
- **Sem `pickle`, `eval`, `exec`, `os.system`, `subprocess`** em lugar nenhum. Celery em
  JSON puro.
- **Sem `dangerouslySetInnerHTML`** em dado de usuário; nenhum renderizador de Markdown.
- **Sem path traversal**: caminhos de arquivo são montados só com UUIDs gerados por nós.
- Chaves de LLM por tenant criptografadas com Fernet e mascaradas na saída.
- `require_admin` no nível do *router* em `/bot`, `/channels`, `/reports`, `/users` — rota
  nova nasce protegida.

---

## Críticas

### C1 — Injeção de mensagens cross-tenant sem autenticação

**Onde:** `app/api/webhooks.py`, `app/channels/whatsapp.py:47`, `app/services/ingestion.py`,
`.env:19`

A validação do webhook era uma linha:

```python
def verify_webhook(self, headers: dict, body: bytes) -> bool:
    return headers.get("apikey") == settings.EVOLUTION_API_KEY
```

Três problemas somados:

1. O valor em uso era o literal **`changeme`** — idêntico ao publicado em `.env.example:15`
   e ao fallback do `docker-compose.yml:40`. Não é segredo fraco: é segredo publicado.
2. Uma **única chave para toda a plataforma**, sem HMAC sobre o corpo, sem timestamp e sem
   nonce (replay livre).
3. Pior de tudo: o **tenant era escolhido pelo corpo da requisição**. `whatsapp.py:71` lia
   `payload["instance"]` e `ingestion.py` resolvia a conexão por
   `credentials->>'instance'`, sem filtro de tenant.

**Exploração:** qualquer um que alcançasse a API fazia `POST /webhooks/whatsapp` com
`apikey: changeme` e um `instance` escolhido, e conseguia, em **qualquer** tenant: forjar
mensagens de cliente, criar contatos e conversas, disparar o agente (gastando a chave de
LLM daquele salão) e fazer o bot **enviar WhatsApp para números arbitrários**. Era também o
vetor de entrada de C2 e H6.

**Correção:**
- Segredo **por conexão**: `provision` gera um `webhook_id` de 256 bits
  (`secrets.token_urlsafe(32)`), guardado em `ChannelConnection.credentials` (JSONB — sem
  migration de schema, conforme a convenção do projeto), e registra no Evolution a URL
  `/webhooks/whatsapp/{webhook_id}`.
- A rota resolve a conexão — e portanto o tenant — **pelo `webhook_id`**, e passa o
  `connection_id` para a task. O `instance` do corpo deixou de decidir qualquer coisa e
  virou um dado *conferido*: se divergir da conexão autenticada, o evento é descartado.
- A `apikey` compartilhada continua como primeira barreira (agora com
  `hmac.compare_digest`), provando a origem. São duas barreiras independentes.
- Guarda de boot em `app/core/config.py`: fora de `ENV=dev`, o processo **aborta** se
  `SECRET_KEY` ou `EVOLUTION_API_KEY` estiverem com valor de exemplo ou abaixo de 32
  caracteres.
- `docker-compose.yml` perdeu o fallback `:-changeme`.

**Testes:** `tests/test_webhook_auth.py`

### C2 — XSS armazenado via mídia recebida → roubo da sessão do atendente

**Onde:** `app/api/media.py:74-82`, `app/tasks/media.py:84`, `app/channels/whatsapp.py:128`

A rota servia o arquivo com `media_type=r.mimetype`, e esse `mimetype` **vinha do payload do
canal** — ou seja, de quem mandou a mensagem. E o `Content-Disposition` nunca era emitido
para mídia recebida: o código só definia `filename` para `content_type == "DOCUMENT"`, e o
worker nem gravava `filename` no metadata, então ele era sempre `NULL`. Sem `filename`, o
`FileResponse` não emite o header e o navegador renderiza inline.

`X-Content-Type-Options: nosniff` **não protegia**: ele impede o navegador de *adivinhar* o
tipo, e aqui o tipo declarado já era o escolhido pelo atacante.

**Exploração:** um estranho manda no WhatsApp do salão um `documentMessage` com
`mimetype: "text/html"` e bytes de uma página HTML. Na thread, o front renderiza
`<a href={src} target="_blank">`. O atendente clica; o HTML do atacante **executa na origem
da API** — e o `location.search` da própria página continha o **JWT do atendente**, porque a
rota aceitava `?token=`. Exfiltrar é uma linha. Com o refresh token de 30 dias no
`localStorage` (H3), virava comprometimento durável.

**Correção:**
- `attachments.mimetype_para_servir()`: o `Content-Type` passa a ser decidido **pelo
  servidor**. O valor guardado só sobrevive se estiver na allow-list `_ACEITOS` *e* for
  coerente com o `ContentType` da mensagem. Qualquer outra coisa vira
  `application/octet-stream`.
- `Content-Disposition: attachment` para tudo que não seja do conjunto seguro de exibição
  inline (JPEG, PNG, WebP, áudios, MP4, 3GPP).
- `Content-Security-Policy: default-src 'none'; sandbox` na resposta de mídia — defesa em
  profundidade, mata a execução mesmo que um tipo escape da allow-list.
- **O JWT saiu da URL.** `app/services/media_links.py` assina o par `(recurso, expiração)`
  com HMAC-SHA256 derivado do `SECRET_KEY`; a assinatura vale por 6 horas, cobre um único
  arquivo e **não autoriza mais nada na API**. O `media_url` e o `arquivo_url` já chegam
  assinados do servidor; o front só acrescenta o host.
- Nome de arquivo do remetente é saneado antes de entrar no header.

**Testes:** `tests/test_media_auth.py` (classes `TestAutenticacaoDaMidia` e
`TestContentTypeNaoEDoRemetente`)

---

## Altas

### H1 — `instance` sem validação, interpolado em URL da API do Evolution

`app/schemas.py` declarava `instance: str`, e esse valor é interpolado no **caminho** de
nove chamadas ao Evolution (`/instance/delete/{instance}`, `/webhook/set/{instance}`…),
todas com a chave **master**, compartilhada por todos os tenants.

O httpx normaliza segmentos `..` antes de enviar (verificado: `httpx.URL(base +
'../../manager/instances').path == '/manager/instances'`). Um admin de tenant que criasse
uma conexão com `instance = "../../instance/delete/salao-vizinho"` não estava escolhendo um
nome — estava escolhendo outra rota administrativa, no Evolution do vizinho.

**Correção:** `pattern=r"^[A-Za-z0-9_-]{3,64}$"` no schema. Ponto único, fecha as nove
interpolações. **Testes:** `tests/test_channels_instance.py`, que documenta também o
mecanismo de normalização do httpx para que ninguém afrouxe o regex sem entender por quê.

### H2 — Nenhum rate limiting em lugar nenhum

Não havia limite em nada: nem dependência no `requirements.txt`, nem middleware.

- `POST /auth/token` era adivinhação de senha ilimitada **e** exaustão de CPU barata: cada
  tentativa queima um bcrypt de custo 12 no event loop.
- `/webhooks/*` dispara o agente, e cada evento vira chamada de LLM paga. Os limites que
  existiam (`AI_MAX_USER_TURNS=8`, `POST_HANDOFF_MAX_TURNS=10`) são **por conversa** — e
  `ingestion` abre conversa nova para cada número de remetente e outra assim que a anterior
  é resolvida. Cada número comprava um orçamento inteiro.

**Correção:** `app/core/ratelimit.py`, janela fixa sobre o Redis que o projeto já tem
(sem dependência nova). Login limitado por IP **e** por e-mail (o segundo cobre o ataque
distribuído contra uma conta); webhook limitado por IP e por conexão; e um **teto diário de
turnos de LLM por tenant** (`AI_MAX_TURNS_DIA`, default 500) no orquestrador — o único teto
real de gasto, porque não depende de quantas conversas o atacante abre.

Falha **aberta** de propósito: Redis fora do ar não pode trancar o dono para fora do painel.

**Testes:** `tests/test_ratelimit.py`

### H3 — Refresh token de 30 dias, nunca invalidável

`/auth/refresh` rotacionava o par lendo só o payload do token. Não havia `jti`, denylist,
logout nem troca de senha — **nenhuma rota do sistema permitia trocar a própria senha**.
Um refresh copiado do `localStorage` era acesso por um mês, e o dono não tinha nenhuma ação
disponível para cortá-lo.

**Correção:** coluna `token_version` em `users` (migration `e7b3d5a91f24`), embutida no JWT
e conferida no `/auth/refresh`. Rotas novas `POST /auth/logout` e `POST /auth/password`,
ambas incrementando a coluna — o que derruba na hora todos os refresh tokens do usuário.
`REFRESH_TOKEN_EXPIRE_DAYS` reduzido de 30 para 7. O front agora chama o logout do servidor
em vez de só limpar o `localStorage`.

Limite conhecido e aceito: o **access token** corrente continua válido até expirar (máx. 60
min). É a mesma troca que o `require_admin` já documentava — checar o banco a cada request
nas rotas quentes da inbox sairia caro.

**Testes:** `tests/test_sessao.py`

### H4 — `instance` sem unicidade global e roteamento inbound ambíguo

`whatsapp_instance_exists` checava unicidade **só dentro do tenant**, e não havia constraint
nenhuma no banco. Mas o nome da instância é global no Evolution, que é um só para toda a
plataforma. Dois tenants com o mesmo `instance` disputavam a mesma instância lá, e
`ingestion` resolvia a conexão por esse campo com `LIMIT 1` sem `ORDER BY`: qual tenant
recebia a mensagem virava decisão do planejador do Postgres.

**Correção:** índice único parcial em `(credentials->>'instance')` para `WHATSAPP`
(migration `c9f4a7b21e63`) e checagem global no serviço. Com C1, o `instance` também deixou
de ser a chave de roteamento.

### H5 — Chave do Evolution e conteúdo das mensagens em texto claro no Redis

`webhooks.py` fazia `ingest_webhook.delay(channel, headers, body)` com o dicionário
**completo** de headers — incluindo a `apikey` que acabara de ser validada. Ela ficava em
texto claro na fila do broker, visível a qualquer `redis-cli MONITOR` e a qualquer traceback
que ecoasse os argumentos da task.

**Correção:** os headers deixaram de ser enviados. Verifiquei que nenhum `_parse_*` do
provider os lê. A task passou a receber o `connection_id`, que é o que ela realmente precisa.

### H6 — Prompt injection com efeito em ações reais

Tudo que alimenta o prompt vem de quem manda mensagem: o texto, o `pushName` (nome do perfil,
texto livre) e o `summary` que o próprio modelo escreveu a partir daquele texto — e que
volta como `last_summary` em **toda conversa futura** com aquele contato. Não havia
delimitação nenhuma entre instrução e dado.

O efeito não era só conversacional. `_acao_pre_reserva` **segura uma data real por 48 h**, e
o único guard era `pre_reserva_id` na sessão — que é **por conversa**. Como abrir conversa é
grátis para quem manda a mensagem, o guard não era teto de nada: um texto que fizesse o
modelo emitir a ação, repetido a partir de vários números, congelava a agenda vendável
inteira.

**Correção:**
- Bloco `_LIMITE_DE_CONFIANCA` no system prompt, declarando explicitamente que histórico,
  nome do contato e resumo anterior são **dado de cliente, não instrução** — e que preço e
  disponibilidade vêm sempre da agenda, que nenhuma mensagem altera. Fica antes da persona,
  para valer também sobre o que vem depois.
- `pushName` saneado na fronteira (`whatsapp.py`): 60 caracteres, sem quebras de linha nem
  caracteres de controle.
- Teto de tamanho por mensagem no histórico (2000 caracteres) — antes, uma mensagem grande
  repetida empurrava o CONHECIMENTO e a persona para fora da janela de contexto.
- Teto de pré-reservas do bot por tenant/dia (`MAX_PRE_RESERVAS_BOT_DIA = 10`), contado no
  banco. Canceladas contam: o estrago de segurar a data já aconteceu.

A regra 3 do `CLAUDE.md` segue intacta — o teto é uma **recusa antes** do INSERT, não uma
autorização; a atomicidade continua sendo do índice parcial único.

**Testes:** `tests/test_agente_hostil.py`

### H7 — Download de mídia recebida sem teto de tamanho

`fetch_media` decodificava o base64 devolvido pelo Evolution e `tasks/media.py` escrevia em
disco, sem limite nenhum entre a rede e o arquivo. Os tetos por tipo existiam apenas para o
anexo que o atendente **envia**.

**Correção:** `attachments.teto_para()` — a mesma tabela de limites passa a valer nos dois
sentidos, conferida **antes** do `b64decode` para não materializar o excesso em memória.

---

## Médias

| # | Achado | Situação |
|---|---|---|
| M1 | `transfer` aceitava `to_user_id` de outro tenant; a conversa sumia de todas as filas | **corrigido** — destino conferido no WHERE (`assignment.py`) |
| M2 | Login sem desambiguação: `LIMIT 1` sem `ORDER BY`, e o e-mail só é único dentro do tenant | **corrigido** — `ORDER BY created_at` torna determinístico |
| M5 | `_apply_status` fazia UPDATE sem predicado de `tenant_id` | **corrigido** — escopo via `connection_id` |
| M3 | `SECRET_KEY` acumula assinar JWT e derivar a chave Fernet das chaves de LLM. Um vazamento faz as duas coisas, e rotacionar apaga as chaves guardadas em silêncio (`decrypt_secret` engole `InvalidToken`) | aberto — separar em `ENCRYPTION_KEY` própria |
| M4 | SQL com parâmetros no stdout | corrigido na consolidação — echo desligado por padrão e parâmetros ocultos |
| M6 | `frontend/.env.local` rastreado | corrigido na árvore atual; histórico privado preservado, candidato público sem esse arquivo |
| M7 | Middleware do Next é no-op e o matcher não cobre `/agenda`, `/agente`, `/equipe`, `/espaco`, `/relatorios`, `/respostas` | aberto — sem impacto real (toda rota da API valida o JWT no servidor), mas o arquivo engana quem lê |
| M8 | `/docs` e `/openapi.json` abertos | desligados fora de dev na consolidação |
| M9 | `channel_metadata.raw` guarda o envelope completo do Evolution para sempre (telefones, device metadata) | aberto — decisão de retenção/LGPD, não bug |

## Baixas

| # | Achado | Situação |
|---|---|---|
| L1 | Comparação de segredo não constant-time | **corrigido** — `hmac.compare_digest` |
| L2 | `/webhooks/instagram` gerava 500 não autenticado; corpo lido sem teto antes da auth | **corrigido** — 404 e cap de 1 MB em streaming |
| L6 | `messageTimestamp` não numérico derrubava a task de ingestão | **corrigido** — `_timestamp()` com fallback |
| L3 | Senha de 8 caracteres, sem complexidade nem verificação de vazamento | aberto |
| L4 | `get_current_user` não consulta o banco: usuário desativado segue válido até o access token expirar | aberto, consciente — ver H3 |
| L5 | CORS com `allow_credentials=True` e `allow_methods/headers=["*"]` | aberto — baixo impacto (auth é Bearer, não cookie) |

---

## Passos de deploy

Ordem importa. Os dois primeiros itens são obrigatórios.

1. **Gerar segredos reais** e pôr no `.env` de produção:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # EVOLUTION_API_KEY
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # SECRET_KEY
   ```
   Trocar o `SECRET_KEY` invalida as chaves de LLM já guardadas pelos tenants (M3) e todas
   as sessões — planeje para antes de haver dados.
   Com `ENV=prod`, o boot aborta se algum deles ficar com valor de exemplo.

2. **`ENV=prod`** no `.env` (desliga o `echo` do SQLAlchemy — M4) e **`TRUSTED_PROXY=true`**
   (o rate limit por IP depende disso atrás do nginx).

3. `alembic upgrade head` — duas migrations novas (`c9f4a7b21e63`, `e7b3d5a91f24`).

4. **Reprovisionar as conexões de WhatsApp** pelo painel, para que ganhem `webhook_id` e o
   Evolution passe a entregar na rota nova. Quem ainda falta:
   ```sql
   SELECT tenant_id, credentials->>'instance'
   FROM channel_connections
   WHERE channel = 'WHATSAPP' AND NOT (credentials ? 'webhook_id');
   ```
   Quando essa consulta voltar vazia, **remover a rota legada** `POST /webhooks/{channel}`
   de `app/api/webhooks.py` — é o último lugar onde o tenant ainda sai do corpo.

5. `git rm --cached frontend/.env.local` (M6).

6. No nginx: TLS, HSTS, `limit_req` por IP como primeira barreira e
   `client_max_body_size` compatível com o maior anexo (20 MB).

---

## Como verificar

```bash
ruff check . && pytest          # 329 testes
cd frontend && npm run check
```

Verificação manual, ponta a ponta:

1. `POST /webhooks/whatsapp/{webhook_id}` com a apikey errada → **401**.
2. O mesmo com o `webhook_id` de outra conexão → o evento cai na conexão dona daquele
   segredo, nunca na do `instance` do corpo.
3. Gravar uma mensagem com `mimetype = "text/html"` e abrir `GET /media/{id}` →
   `application/octet-stream` + `Content-Disposition: attachment`; o navegador baixa, não
   executa.
4. Criar conexão com `instance = "../../instance/delete/outro"` → **422**.
5. Seis logins errados seguidos → **429**.
6. Logout e, em seguida, tentar o refresh token antigo → **401**.
7. Mandar mensagem no WhatsApp de teste e confirmar que a thread ainda mostra imagem, toca
   áudio e abre documento — o caminho da mídia foi o que mais mudou.
