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
ssh "${SERVER_USER}@${SERVER_HOST}" <<EOF
  set -euo pipefail
  cd ~/languageLearningApp

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
    echo "🚀 Starting db + web..."
    docker compose up -d db web

    echo "⏳ Waiting briefly for db..."
    sleep 2

    echo "📦 Running migrations..."
    docker compose exec -T web flask --app app:create_app db upgrade

    echo "🔄 Bringing up full stack..."
    docker compose up -d
  fi

  echo "✅ Deploy complete"
EOF
