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
git pull --ff-only origin $BRANCH

echo "⬆️ Pushing to GitHub..."
git push origin $BRANCH

echo "🚀 Deploying to server..."
ssh ${SERVER_USER}@${SERVER_HOST} <<'EOF'
  set -e
  cd ~/languageLearningApp

  echo "📥 Pulling code..."
  git pull --ff-only

  echo "🐳 Rebuilding containers..."
  docker compose build web

  echo "📦 Running migrations..."
  docker compose exec -T web python -m flask db upgrade

  echo "🔄 Restarting services..."
  docker compose up -d

  echo "✅ Deploy complete"
EOF
