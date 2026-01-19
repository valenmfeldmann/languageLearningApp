#!/usr/bin/env bash
set -euo pipefail

SERVER_USER=app
SERVER_HOST=143.198.15.118
SERVER_DIR=languageLearningApp
BRANCH=main

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
ssh "${SERVER_USER}@${SERVER_HOST}" <<'EOF'
  set -euo pipefail
  cd ~/languageLearningApp

  echo "📥 Pulling code..."
  git pull --ff-only

  echo "🐳 Building images..."
  docker compose build

  echo "🚀 Starting db + web..."
  docker compose up -d db web

  echo "⏳ Waiting briefly for db..."
  sleep 2

  echo "📦 Running migrations..."
  # Use explicit --app in case FLASK_APP isn't set in env
  docker compose exec -T web flask --app app:create_app db upgrade

  echo "🔄 Bringing up full stack (including worker if defined)..."
  docker compose up -d

  echo "✅ Deploy complete"
EOF
