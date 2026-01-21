#!/usr/bin/env bash
set -euo pipefail

echo "==> Migrating DB..."
docker compose run --rm web flask --app app:create_app db upgrade
echo "==> Done."
