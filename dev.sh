#!/bin/bash
set -e

G='\033[0;32m' Y='\033[0;33m' C='\033[0;36m' R='\033[0;31m' B='\033[1m' N='\033[0m'

pfx() { local p=$1 c=$2; while IFS= read -r l; do echo -e "${c}[${p}]${N} $l"; done; }

cleanup() {
  echo -e "\n${R}Encerrando...${N}"
  kill $(jobs -p) 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup SIGINT SIGTERM

echo -e "${B}▶ Otto dev${N}"

if [[ ! -x .venv/bin/python ]]; then
  echo "Crie .venv e instale requirements-dev.lock com --require-hashes antes de iniciar."
  exit 1
fi
export PATH="$PWD/.venv/bin:$PATH"

echo -e "${G}[DOCKER]${N} Subindo serviços..."
docker compose up -d postgres redis
if [[ "${WITH_EVOLUTION:-0}" == "1" ]]; then
  docker compose -f docker-compose.yml -f docker-compose.whatsapp.yml --profile whatsapp up -d evolution
fi

echo -e "${G}[DOCKER]${N} Aguardando PostgreSQL..."
until docker compose exec -T postgres pg_isready -U otto -d otto &>/dev/null; do
  sleep 1
done
echo -e "${G}[DOCKER]${N} PostgreSQL pronto."

echo -e "${G}[MIGRATE]${N} Aplicando migrations..."
alembic upgrade head 2>&1 | pfx "MIGRATE" "$G"

echo -e "${B}Iniciando serviços...${N}"
echo -e "  API      → http://localhost:8001"
echo -e "  Docs     → http://localhost:8001/docs"
echo -e "  Frontend → http://localhost:3002"
echo -e "  Worker   → (background)"
echo ""

uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 2>&1 | pfx "API    " "$G" &
celery -A app.worker.celery_app worker --loglevel=warning 2>&1 | pfx "WORKER " "$Y" &
(cd frontend && npm run dev) 2>&1 | pfx "FRONT  " "$C" &

wait
