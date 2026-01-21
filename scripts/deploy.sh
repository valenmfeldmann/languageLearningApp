#!/usr/bin/env bash
set -euo pipefail

SERVER_USER=app
SERVER_HOST=143.198.15.118
SERVER_DIR=languageLearningApp
BRANCH=main

RESET_DB="${RESET_DB:-0}"

echo "🔍 Checking git status..."
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "❌ You have uncommitted changes. Commit first."
  exit 1
fi

echo "⬇️ Pulling latest from origin..."
git pull --ff-only origin "$BRANCH"

echo "⬆️ Pushing to GitHub..."
git push origin "$BRANCH"

echo "🚀 Deploying to server..."
ssh "${SERVER_USER}@${SERVER_HOST}" "RESET_DB=${RESET_DB} SERVER_DIR=${SERVER_DIR}" <<'EOF'
  set -euo pipefail
  cd ~/"$SERVER_DIR"

  echo "📥 Pulling code..."
  git pull --ff-only

  echo "🐳 Building images..."
  docker compose build

  # Ensure DB is up (or reset it)
  if [ "$RESET_DB" = "1" ]; then
    echo "💣 RESET_DB=1 — wiping DB volume (SERVER) and rebuilding from committed migrations"
    docker compose down -v
  fi

  echo "🗄️ Starting db..."
  docker compose up -d db

  echo "⏳ Waiting for db health..."
  DB_CID="$(docker compose ps -q db)"
  if [ -z "$DB_CID" ]; then
    echo "❌ Could not find db container id"
    docker compose ps
    exit 1
  fi

  status=""
  for i in {1..30}; do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$DB_CID" 2>/dev/null || true)"
    if [ "$status" = "healthy" ]; then
      echo "✅ DB is healthy"
      break
    fi
    sleep 2
  done

  if [ "$status" != "healthy" ]; then
    echo "❌ DB never became healthy (status='$status')"
    docker compose logs --tail=200 db
    exit 1
  fi

  echo "🛑 Stopping web/worker to avoid weirdness during upgrade..."
  docker compose stop web worker || true

  echo "📦 Running migrations..."
  ./scripts/migrate.sh

  echo "🌱 Seeding core invariants..."
  ./scripts/seed_core.sh

  echo "🚀 Starting app services..."
  docker compose up -d --no-deps --force-recreate web worker
  docker compose up -d nginx

  echo "✅ Deploy complete"
EOF
