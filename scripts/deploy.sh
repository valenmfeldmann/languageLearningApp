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

  if [ "$RESET_DB" = "1" ]; then
    echo "💣 RESET_DB=1 — performing hard DB reset from models"

    docker compose down -v
    docker compose up -d

    ./scripts/hard_reset_schema_from_models.sh
  else
    echo "🗄️ Ensuring db is up..."
    docker compose up -d db

    echo "⏳ Waiting for db health..."
    status=""
    for i in {1..30}; do
      status="$(docker inspect -f '{{.State.Health.Status}}' languagelearningapp-db-1 2>/dev/null || true)"
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

    echo "🛑 Stopping web to avoid migration locks..."
    docker compose stop web || true

    echo "📦 Running migrations..."
    docker compose run --rm web flask --app app:create_app db upgrade

    echo "🚀 Starting app services..."
    docker compose up -d --no-deps --force-recreate web worker
    docker compose up -d nginx
  fi

  echo "✅ Deploy complete"
EOF
