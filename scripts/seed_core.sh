#!/usr/bin/env bash
set -euo pipefail

SQL_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/seed_core.sql"

if [[ ! -f "$SQL_FILE" ]]; then
  echo "Missing SQL file: $SQL_FILE" >&2
  exit 1
fi

echo "==> Seeding using $SQL_FILE"

# Mode A: Use DATABASE_URL if provided (server-friendly)
if [[ "${DATABASE_URL:-}" != "" ]]; then
  echo "==> Using DATABASE_URL"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 < "$SQL_FILE"
  echo "==> Done."
  exit 0
fi

# Mode B: Docker compose db container (local-friendly)
echo "==> DATABASE_URL not set. Using docker compose exec db..."
cat "$SQL_FILE" | docker compose exec -T db psql -U postgres -d appdb -v ON_ERROR_STOP=1
echo "==> Done."
