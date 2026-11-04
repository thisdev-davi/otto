# Otto

Inbox de WhatsApp com múltiplos atendentes, agente de IA e transferência para humano.
O projeto inclui um exemplo fictício de salão de festas: agenda por data, pré-reserva,
confirmação pela equipe e galeria do espaço.

O agente consulta conhecimento e agenda; o roteiro determinístico atende sem LLM.
Ambos usam a mesma ingestão, fila de atendimento e persistência. O copilot sugere uma
resposta para o atendente e nunca envia sozinho.

## Executar

Precisa de Python 3.12, Node.js 22, Docker Compose e `uv` ou `pip`.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r requirements-dev.lock
cp .env.example .env
cp frontend/.env.local.example frontend/.env.local
# Preencha SECRET_KEY e EVOLUTION_API_KEY com segredos próprios.
cd frontend
npm ci
cd ..
./dev.sh
```

Abra [localhost:3002](http://localhost:3002). A API usa a porta 8001,
PostgreSQL 5437 e Redis 6382. `dev.sh` aplica as migrations e inicia API, worker e frontend.
Os exemplos de ambiente usam essas mesmas portas.

Para criar uma conta, no ambiente virtual:

```sh
python scripts/criar_conta.py --nome "Salão Exemplo" --email admin@example.com
```

O script gera a senha. Para explorar a interface com dados fictícios, sem parear WhatsApp:

```sh
python seed_test_account.py
```

Esse seed **recria a conta Otto Teste**, com login `teste@otto.com` e senha `teste1234`.
Use somente no seu banco local de testes; ele deixa o bot desligado. As conversas
preenchidas permitem explorar a interface, mas não simulam transporte WhatsApp.

### WhatsApp e IA

O WhatsApp real exige Evolution externo, pareamento e webhook público alcançável pela
Evolution. Configure `EVOLUTION_URL`, `EVOLUTION_API_KEY` e `WEBHOOK_BASE_URL`.
Para executar Evolution por Compose, informe uma imagem que você possa obter/construir:

```sh
EVOLUTION_IMAGE=seu-registry/evolution:sua-versao WITH_EVOLUTION=1 ./dev.sh
```

A antiga imagem `evolution-api-lid:2.2.3` era local e não é uma dependência baixável
presumida. A interface pode ser explorada sem ela; envio e recebimento reais precisam
do serviço configurado.

`LLM_PROVIDER=stub` permite testar o contrato de triagem sem chamadas pagas. O stub
não substitui o transporte WhatsApp nem demonstra a qualidade semântica de um modelo.
Agente, embeddings e transcrição usam as chaves e provedores configurados na conta.

![Inbox do Otto com dados fictícios](docs/images/inbox.png)

## Como funciona

```text
WhatsApp → adapter de canal → FastAPI → PostgreSQL
                                ↓
                          Redis / Celery
                                ↓
                     fluxo ou agente de IA
                                ↓
                    ações de agenda / handoff
                                ↓
                      adapter → WhatsApp

Next.js ← WebSocket / API ← PostgreSQL + mídia local ou S3 privado
```

- Ingestão idempotente: reentregas não criam outra mensagem.
- Captura, encaminhamento e reserva disputados no banco: duas requisições concorrentes
  não ganham a mesma conversa ou data.
- Preço e disponibilidade vêm da agenda transacional. O agente cria pré-reserva;
  a confirmação é humana.
- Dados coletados se acumulam e a persona define a condução. O bot continua ajudando
  após encaminhar, até alguém assumir, respeitando os limites de uso.
- RAG usa PostgreSQL/pgvector; falha de embedding permite busca textual. Fontes e
  limites de relevância não garantem que uma resposta do modelo esteja correta.
- Arquivos são servidos após autorização da aplicação. O bucket S3 permanece privado;
  arquivos locais existentes continuam legíveis durante a migração.

## Auditoria por conta

Administradores acessam **Configurações → Auditoria**, com filtros, paginação e CSV.
O contrato e os limites operacionais estão em [Auditoria](docs/AUDITORIA.md).
A trilha registra ator, ação, recurso, resultado, motivo e correlação; alterações no
banco e seu registro são atômicos. Intenção e resultado dos envios externos são
separados: um timeout pode deixar resultado incerto, sem reenvio automático.

O registro não copia conversas, prompts ou segredos. IPs são mascarados; a retenção
é de 180 dias, com limpeza coordenada enquanto a API estiver ativa e na inicialização.
Esse prazo vale para a auditoria, não para conversas e anexos. O produto não permite
editar ou apagar eventos; administradores diretos do banco continuam tendo poder
sobre seus dados. Auditoria auxilia a rastreabilidade, sem certificar conformidade
com a LGPD. Falhas de registro de leitura são sinalizadas no log técnico; alterações
no banco falham se não puderem registrar auditoria.

## Conteúdo e operação

Persona, conhecimento e roteiro de exemplo vivem em `conteudo/`.

```sh
python scripts/sincronizar_conteudo.py --check
python scripts/sincronizar_conteudo.py --conta "Salão Exemplo" --dry-run
python scripts/sincronizar_conteudo.py --conta "Salão Exemplo"
scripts/otto.sh status
```

`scripts/otto.sh restart` **descarta a fila pendente** por convenção de desenvolvimento.
Não use esse comando como estratégia de atualização em produção. No deploy, migrations
são uma etapa explícita e workers devem encerrar com tratamento das tarefas pendentes.

## Testes

A suíte exige banco e Redis exclusivos, `ENV=test` e impede HTTP externo real.
O passo a passo está em [Validação](docs/VALIDACAO.md).

```sh
pytest -q
ruff check .
ruff format --check .
cd frontend
npm run check
npm run build
```

A CI instala dependências pelo lock, aplica migrations em PostgreSQL/pgvector
isolado e verifica backend, tipos, tokens, contraste e build. Resultados locais
medidos ficam em [Validação](docs/VALIDACAO.md), sem confundir testes automatizados
com ensaio de WhatsApp ou deploy real.

## Deploy e estado

A configuração em `render.yaml` prepara frontend, API, worker e Redis no Render,
com banco Neon e armazenamento compatível com S3. Evolution é externo. Não há VPS
presumida e a stack completa não é apresentada como gratuita.

[Deploy](docs/DEPLOY.md) descreve segredos, TLS, conexões direta/aplicação, mídia e
verificação de saúde. A configuração foi preparada para implantação futura;
nenhum deploy remoto foi executado nesta consolidação.

[Segurança](SEGURANCA.md) distingue o laudo
histórico das verificações atuais. Avaliação pós-atendimento permanece em
[Melhorias](docs/MELHORIAS.md).

## Desenvolvimento e licença

O desenvolvimento usa assistência de Claude Code/Codex. Contratos, decisões,
revisão do código, testes e verificações de interface fazem parte do processo;
a documentação registra o que foi medido e as limitações da validação.

MIT para o material próprio ou autorizado, com avisos de terceiros preservados.
