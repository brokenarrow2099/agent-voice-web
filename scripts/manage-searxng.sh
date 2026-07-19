#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${SEARXNG_COMPOSE_FILE:-$HOME/searxng/docker-compose.yml}"
[[ -s "$COMPOSE_FILE" ]] || {
  echo "Missing SearXNG Compose file: $COMPOSE_FILE" >&2
  exit 1
}

COMPOSE=(docker compose --project-directory "$(dirname "$COMPOSE_FILE")" -f "$COMPOSE_FILE")

start_searxng() {
  for _ in {1..60}; do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
  docker info >/dev/null 2>&1 || {
    echo "Docker did not become available within 60 seconds" >&2
    exit 1
  }

  "${COMPOSE[@]}" up -d searxng
  for _ in {1..60}; do
    curl --silent --fail http://127.0.0.1:8081/healthz >/dev/null && return 0
    sleep 1
  done
  echo "SearXNG did not become healthy within 60 seconds" >&2
  exit 1
}

stop_searxng() {
  "${COMPOSE[@]}" stop searxng
}

case "${1:-}" in
  start) start_searxng ;;
  stop) stop_searxng ;;
  *) echo "Usage: $0 {start|stop}" >&2; exit 2 ;;
esac
