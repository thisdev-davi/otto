# Validação isolada

Não execute a suíte no banco usado para testar manualmente ou atender clientes.
Os testes criam e removem registros e limpam contadores no Redis. O início da suíte
recusa ambiente diferente de `test` ou nome de banco sem `test`.

```sh
docker compose -f docker-compose.test.yml up -d --wait
. .venv/bin/activate
export ENV=test
export DATABASE_URL=postgresql+asyncpg://otto:otto@localhost:55437/otto_test
export REDIS_URL=redis://localhost:56382/0
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export EVOLUTION_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export EVOLUTION_URL=http://127.0.0.1:58084
export LLM_PROVIDER=stub
export LLM_API_KEY=''
export ANTHROPIC_API_KEY=''
export EMBEDDINGS_API_KEY=''
export TRANSCRIPTION_API_KEY=''
alembic upgrade head
pytest -q
ruff check .
ruff format --check .
cd frontend
npm ci
npm run check
npm run build
```

As chaves geradas são exclusivas deste ambiente de teste. A fixture de rede impede chamadas
HTTP externas reais; testes de transporte usam fakes, ASGI ou mocks. Armazenamento
S3 é exercitado com moto, incluindo privacidade, migração e leitura entre processos.

## Resultados desta consolidação

Verificado em 03/11/2026, Python 3.12 e dependências de `requirements-dev.lock`:

- Suíte completa: **639 testes aprovados**, em 56,28 s; um aviso de depreciação
  do código HTTP 422 no Starlette, sem falha de teste.
- Ruff: lint e formatação aprovados (198 arquivos Python).
- Frontend: tipos, tokens e 42 pares de contraste em cada tema aprovados; build
  de produção com 18 rotas aprovado.
- Migrações: banco vazio e cópia restaurada de banco preenchido. As 193 datas,
  6 reservas, 3 fotos e mensagens de confirmação permaneceram iguais.
- Navegador: login, nova navegação, auditoria, agenda e galeria; temas claro/escuro
  e viewport de 390 px sem rolagem horizontal no documento. Evidências usam dados fictícios.
- Gitleaks 8.30.1: nenhuma ocorrência no histórico completo alcançável da origem;
  árvore candidata também deve passar no scanner antes de receber `READY.json`.
- Compose local configurável sem imagem Evolution; o overlay WhatsApp só é
  carregado quando solicitado.

As verificações não incluem provisionamento no Render/Neon nem pareamento de um
WhatsApp real. O ensaio de interface e os testes HTTP não comprovam a qualidade
semântica de um modelo nem entrega de mensagens por uma conexão real.
