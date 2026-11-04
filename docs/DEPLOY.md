# Deploy futuro: Render + Neon

O `render.yaml` instala as dependências de runtime fixadas em `requirements.lock`,
com verificação de hashes, e prepara frontend Next.js, API FastAPI, worker Celery e Render Key Value.
O banco Neon, o bucket privado e o provedor Evolution são externos. Nenhum recurso foi
provisionado ou validado remotamente nesta consolidação. O Blueprint especifica planos
pagos; confira os custos e a região antes de aplicá-lo.

## Credenciais e banco

1. Crie um projeto PostgreSQL no Neon. A cadeia Alembic habilita `vector`; use um papel
   autorizado a criar a extensão e guarde backup antes de atualizar um banco existente.
2. Defina `DATABASE_DIRECT_URL` com a conexão **direta**, sem `-pooler`, para migrations.
   Exemplo fictício: `postgresql://usuario:senha@ep-exemplo.us-east-2.aws.neon.tech/otto?sslmode=require`.
   `DATABASE_URL` aceita a conexão direta ou pooled; o driver é normalizado para asyncpg,
   e o parâmetro libpq `sslmode` vira `ssl`. Conexões pooled desativam o cache de prepared
   statements. Não retire o TLS. Credenciais com caracteres especiais precisam de URL encoding.
3. Gere `SECRET_KEY` e `EVOLUTION_API_KEY` fortes, com pelo menos 32 caracteres. A primeira
   precisa ser a mesma na API e no worker, pois também cifra chaves de IA dos tenants.
   Não a regenere em redeploys. Guarde segredos no Render, nunca em arquivos versionados.
4. Configure `EVOLUTION_URL` como a URL HTTPS do seu provedor Evolution e `WEBHOOK_BASE_URL`
   como a URL HTTPS **pública da API Otto**. Instâncias existentes precisam ter seus webhooks
   atualizados após a mudança de domínio. O serviço Evolution precisa de banco, Redis e
   persistência próprios, separados do banco e do Key Value do Otto.

## Arquivos compartilhados

API e worker são processos separados. O disco de um serviço Render não é um volume
compartilhado com outro serviço. Use bucket privado compatível com S3, com acesso
`PutObject`, `GetObject` e `HeadObject`; bloqueie acesso público e habilite TLS.

Preencha `MEDIA_STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID` e
`S3_SECRET_ACCESS_KEY`. Para outro provedor S3, preencha também `S3_ENDPOINT_URL` com HTTPS;
ajuste `S3_ADDRESSING_STYLE=path` se o provedor exigir. A API é a única origem de leitura
do navegador, mantendo autorização por tenant, assinaturas curtas, MIME seguro e suporte
a range de áudio/vídeo. Nenhum bucket público ou URL pública permanente é necessário.

Cada arquivo guarda seu próprio locator. Arquivos antigos continuam locais mesmo ao
ativar S3 para novos uploads. Para levá-los ao deploy:

```bash
# No host que possui os arquivos locais, com backup do banco e de data/media.
# Configure S3_* com o bucket de destino. O default é diagnóstico sem alterações.
python -m scripts.migrate_media_storage
# Pause API e worker durante a cópia final e troca de banco/domínio.
python -m scripts.migrate_media_storage --apply
```

O comando compara SHA-256 após cada cópia, atualiza somente referências ainda iguais e
preserva arquivos locais. Rode novamente até `references=0`; `missing>0` exige restaurar
o arquivo original antes de migrar. Mantenha os diretórios e paths originais disponíveis
no host da migração: locators legados podem ser relativos. Não apague a origem até validar
anexos, avatares, galeria, reenvios e transcrição com API e worker separados. Para rollback,
restaure **o banco e os arquivos** do mesmo backup; não basta trocar a variável do backend.

## Blueprint e domínios

Revise `render.yaml` antes de usar **New Blueprint Instance**. Os valores `sync: false`
são preenchidos no painel. O worker referencia os mesmos segredos da API; mantenha
região e demais configurações S3 iguais nos dois serviços. O Key Value usa `noeviction`
para que o broker não descarte tarefas por pressão de memória.

- API: `CORS_ORIGINS=["https://otto-frontend.onrender.com"]`, adaptado ao domínio real.
- Frontend: `NEXT_PUBLIC_API_URL=https://otto-api.onrender.com` e
  `NEXT_PUBLIC_WS_URL=wss://otto-api.onrender.com`. São valores incorporados no build;
  alterá-los requer rebuild. A CSP lê as mesmas origens.
- API: readiness em `/health/ready`. O pre-deploy roda `alembic upgrade head` uma vez
  usando a conexão direta. Primeiro publique schema/API; só então inicie o worker para
  impedir que ele consuma tarefas antes da migration.
- O comando Uvicorn confia nos cabeçalhos do proxy Render. Só use essa configuração
  atrás do proxy controlado pelo serviço. O banco desliga SQL echo por padrão.
- Chaves de chat, embeddings e transcrição podem ser configuradas por conta no painel;
  o default `stub` evita chamadas pagas involuntárias. Nunca use stub como evidência de
  teste real de integração com IA/WhatsApp.

Depois de subir, valide login, isolamento de contas, WebSocket, upload/leitura por URL
assinada, áudio/transcrição, reserva concorrente, confirmação humana e auditoria. Para
testar WhatsApp real é necessário parear o número e verificar entrega de webhook/ACK.
Monitore erros da API/worker, fila, banco e crescimento do bucket. A retenção da auditoria
não elimina anexos nem conversas; defina uma política própria de backup/restauração.

## Evolution local opcional

O Compose sobe PostgreSQL e Redis para desenvolvimento. `EVOLUTION_IMAGE` não tem imagem
customizada presumida: forneça uma imagem de registry acessível, com versão revisada e
compatível com sua sessão e adapter. `WITH_EVOLUTION=1 ./dev.sh` inclui explicitamente `docker-compose.whatsapp.yml` e inicia o perfil WhatsApp.
Sem esse overlay, configurar ou obter uma imagem Evolution não é pré-requisito do ambiente local.
Se usar Evolution externo, apenas configure `EVOLUTION_URL`/chave/webhook. Não há serviço
Evolution implícito no Blueprint Render.

Referências: [Blueprint Render](https://render.com/docs/blueprint-spec),
[discos e isolamento](https://render.com/docs/disks),
[conexões Neon](https://neon.com/docs/connect/connect-from-any-app),
[pooling Neon](https://neon.com/docs/connect/connection-pooling).

## Dependências e validação

Instalação local reproduzível: `python -m venv .venv` seguido por
`.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock`.
`requirements.txt` e `requirements-dev.txt` declaram as dependências diretas; os dois
locks fixam todas as transitivas para Python 3.12. Atualize-os juntos com `uv pip compile`
com `--generate-hashes`; o lock de desenvolvimento deve usar `-c requirements.lock`.
O CI usa PostgreSQL/Redis próprios, aplica migrations em banco vazio, roda testes e
verificações frontend, e varre árvore/histórico com Gitleaks em modo redigido.
