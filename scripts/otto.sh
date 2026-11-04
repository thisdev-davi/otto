#!/usr/bin/env bash
# Controle dos processos de desenvolvimento do Otto: API, worker e fila.
#
# Existe por causa de um incidente real: o worker ficou parado enquanto webhooks
# continuavam chegando, a fila do Celery acumulou, e ao subir o worker ele drenou o
# backlog de uma vez — o bot escreveu do nada para vários contatos em segundos.
#
# Daí as duas regras deste script:
#   1. `purge` SEMPRE para o worker antes de esvaziar a fila. Esvaziar com o worker
#      vivo é inútil: ele consome mais rápido do que você apaga.
#   2. `restart` purga por padrão. Depois de qualquer tempo parado, o que está na fila
#      é passado — e passado, no WhatsApp, vira mensagem inesperada para o cliente.
#
# Uso:
#   scripts/otto.sh panic      # PARA TUDO e esvazia a fila. Use quando estiver saindo mensagem.
#   scripts/otto.sh status     # o que está no ar, quantas tarefas na fila
#   scripts/otto.sh stop
#   scripts/otto.sh start      # sobe API + worker (sem purgar)
#   scripts/otto.sh restart    # stop + purge + start  ← o normal depois de mexer no código
#   scripts/otto.sh restart --keep-queue   # restart sem purgar (só se souber o que há na fila)

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

# Segue o COMPOSE_PROJECT_NAME do .env — com outro nome de projeto, o container
# do compose muda junto e o `purge` falharia em silêncio.

LOG_DIR="${TMPDIR:-/tmp}/otto"
API_LOG="$LOG_DIR/api.log"
WORKER_LOG="$LOG_DIR/worker.log"
CELERY_PATTERN="celery -A app.worker.celery_app"
API_PATTERN="uvicorn app.main:app"
# 8001 é a porta do projeto (README, `frontend/.env.local`, `WEBHOOK_BASE_URL`). Ficava
# escrita duas vezes aqui e as duas discordavam: subia na 8000 e conferia a saúde na 8001.
# O efeito era pior que um erro — o script dizia "API NAO respondeu" e, quando alguém
# ignorava isso, a API estava mesmo no ar, só que numa porta em que ninguém batia: o
# frontend perdia o perfil e a lista de conversas, e o webhook do Evolution caía no vazio.
API_PORT="${API_PORT:-8001}"

if [ -x .venv/bin/python ]; then
  export PATH="$PWD/.venv/bin:$PATH"
fi

mkdir -p "$LOG_DIR"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

fila_tamanho() {
  docker compose exec -T redis redis-cli LLEN celery 2>/dev/null || echo "?"
}

parar_worker() {
  pkill -TERM -f "$CELERY_PATTERN" 2>/dev/null
  for _ in $(seq 1 10); do
    pgrep -f "$CELERY_PATTERN" >/dev/null || return 0
    sleep 1
  done
  # Não saiu no TERM: o worker pode estar no meio de uma chamada ao LLM (até ~20s).
  pkill -KILL -f "$CELERY_PATTERN" 2>/dev/null
  sleep 1
}

parar_api() {
  pkill -TERM -f "$API_PATTERN" 2>/dev/null
  sleep 2
  pkill -KILL -f "$API_PATTERN" 2>/dev/null
}

purgar_fila() {
  # Só é seguro com o worker parado — quem chama garante isso.
  local antes
  antes=$(fila_tamanho)
  docker compose exec -T redis redis-cli DEL celery >/dev/null 2>&1
  # Tarefas com countdown (ex.: a re-tentativa do bot) ficam em chaves separadas.
  docker compose exec -T redis redis-cli --scan --pattern 'unacked*' 2>/dev/null \
    | xargs -r -I{} docker compose exec -T redis redis-cli DEL {} >/dev/null 2>&1
  green "fila esvaziada (${antes} tarefa(s) descartada(s))"
}

subir_worker() {
  nohup celery -A app.worker.celery_app worker --loglevel=info --pool=solo \
    >"$WORKER_LOG" 2>&1 &
  sleep 8
  if pgrep -f "$CELERY_PATTERN" >/dev/null; then
    green "worker no ar  ($WORKER_LOG)"
  else
    red "worker NAO subiu — veja $WORKER_LOG"; tail -20 "$WORKER_LOG"
  fi
}

subir_api() {
  nohup uvicorn app.main:app --reload --host 0.0.0.0 --port "$API_PORT" \
    >"$API_LOG" 2>&1 &
  sleep 8
  if curl -s -o /dev/null --max-time 5 "http://localhost:$API_PORT/docs"; then
    green "API no ar     ($API_LOG)"
  else
    red "API NAO respondeu — veja $API_LOG"; tail -20 "$API_LOG"
  fi
}

case "${1:-}" in
  panic)
    bold "PARANDO TUDO"
    parar_worker
    parar_api
    purgar_fila
    green "nada mais sai para o WhatsApp."
    ;;

  stop)
    parar_worker; parar_api
    green "API e worker parados (fila preservada: $(fila_tamanho) tarefa(s))"
    ;;

  start)
    subir_api
    subir_worker
    ;;

  restart)
    parar_worker
    parar_api
    if [ "${2:-}" = "--keep-queue" ]; then
      bold "fila PRESERVADA: $(fila_tamanho) tarefa(s) serão processadas ao subir"
    else
      purgar_fila
    fi
    subir_api
    subir_worker
    ;;

  status)
    bold "processos"
    pgrep -f "$API_PATTERN"    >/dev/null && green "  API      no ar" || red "  API      parada"
    pgrep -f "$CELERY_PATTERN" >/dev/null && green "  worker   no ar" || red "  worker   parado"
    bold "fila"
    echo "  $(fila_tamanho) tarefa(s) aguardando"
    bold "codigo vs worker"
    # Celery nao recarrega sozinho: worker mais VELHO que o codigo roda a versao antiga.
    if pgrep -f "$CELERY_PATTERN" >/dev/null; then
      local_pid=$(pgrep -f "$CELERY_PATTERN" | head -1)
      # `etimes` (segundos de vida do processo), e nao `lstart`. O `lstart` sai no idioma
      # do sistema — em pt_BR vira "ter set  8 15:20:19 2026" — e o `date -d` recusava,
      # deixando `inicio` vazio. Com o fallback `:-0`, o find comparava com a epoch e
      # TODO arquivo era "mais novo que o worker": o aviso ficava aceso para sempre,
      # inclusive logo depois de um restart. Aviso que nunca apaga ninguem le, e o caso
      # de verdade — worker rodando codigo velho — passava batido.
      etimes=$(ps -o etimes= -p "$local_pid" 2>/dev/null | tr -d ' ')
      inicio=$(( $(date +%s) - ${etimes:-0} ))
      recente=$(find app -name '*.py' -newermt "@${inicio}" 2>/dev/null | head -3)
      if [ -n "$recente" ]; then
        red "  worker DESATUALIZADO — mudou depois dele subir:"
        echo "$recente" | sed 's/^/    /'
        echo "    rode: scripts/otto.sh restart"
      else
        green "  worker com o codigo atual"
      fi
    fi
    ;;

  *)
    sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
