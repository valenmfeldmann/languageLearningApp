#!/usr/bin/env bash
set -euo pipefail

SQL_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/seed_core.sql"

if [[ ! -f "$SQL_FILE" ]]; then
  echo "Missing SQL file: $SQL_FILE" >&2
  exit 1
fi

echo "==> Seeding using $SQL_FILE"
echo "==> Seeding via docker compose exec db psql ..."

docker compose exec -T db bash -lc '
  set -euo pipefail

  U="${POSTGRES_USER:-postgres}"
  D="${POSTGRES_DB:-appdb}"
  PW="${POSTGRES_PASSWORD:-}"

  echo "==> db container: user=$U db=$D"

  # Feed password to psql non-interactively (if present)
  if [[ -n "$PW" ]]; then
    export PGPASSWORD="$PW"
  fi

  # Use TCP so we follow password rules consistently
  psql -h 127.0.0.1 -v ON_ERROR_STOP=1 -U "$U" -d "$D"
' < "$SQL_FILE"

echo "==> Done."
