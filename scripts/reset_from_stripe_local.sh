#!/usr/bin/env bash
set -euo pipefail

DB_NAME="${DB_NAME:-appdb}"
DB_USER="${DB_USER:-postgres}"

echo "==> Truncating ALL tables (except alembic_version) in public schema..."

# Truncate everything except alembic_version (keeps migration state)
cat <<'SQL' | docker compose exec -T db psql -U postgres -d appdb -v ON_ERROR_STOP=1
DO $$
DECLARE
  stmt text;
BEGIN
  SELECT 'TRUNCATE TABLE ' ||
         string_agg(format('%I.%I', schemaname, tablename), ', ') ||
         ' RESTART IDENTITY CASCADE;'
    INTO stmt
  FROM pg_tables
  WHERE schemaname = 'public'
    AND tablename <> 'alembic_version';

  IF stmt IS NULL THEN
    RAISE NOTICE 'No tables found to truncate.';
  ELSE
    EXECUTE stmt;
  END IF;
END $$;
SQL

echo "==> Seeding core invariants (plan, AN asset, system accts, balances)..."
./scripts/seed_core.sh

echo "==> Importing users from Stripe into DB..."
docker compose exec -T web flask --app app:create_app stripe-import-users

echo "==> Seeding again (create user wallets + balances for imported users)..."
./scripts/seed_core.sh

echo "==> Syncing all Stripe state (subs, etc.)..."
docker compose exec -T web flask --app app:create_app stripe-sync-all

echo "==> Final seed pass (optional but harmless)..."
./scripts/seed_core.sh

echo "==> Reset complete."
