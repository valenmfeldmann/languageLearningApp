-- scripts/seed_core.sql
-- Idempotent seed + sanity checks for LanguageLearningApp

BEGIN;

-- ----------------------------
-- PLAN
-- ----------------------------
INSERT INTO "plan" (code, stripe_price_id, monthly_amount_cents, active, created_at)
VALUES ('base', 'price_1SfClWD2igvVKX3LKDnAob3X', 885, true, NOW())
ON CONFLICT (code)
DO UPDATE SET
  stripe_price_id = EXCLUDED.stripe_price_id,
  monthly_amount_cents = EXCLUDED.monthly_amount_cents,
  active = EXCLUDED.active;

-- ----------------------------
-- LEDGER: ASSET + SYSTEM ACCOUNTS
-- ----------------------------

---- 1) asset code should be globally unique
--CREATE UNIQUE INDEX IF NOT EXISTS ux_access_asset_code
--ON access_asset (code);

---- 2) (optional) one share-asset per curriculum
--CREATE UNIQUE INDEX IF NOT EXISTS ux_access_asset_curriculum_share
--ON access_asset (curriculum_id)
--WHERE asset_type = 'curriculum_share';

-- Create AN asset (updates fields if code already exists)
INSERT INTO access_asset (id, code, asset_type, curriculum_id, scale, created_at)
VALUES ('asset_AN', 'AN', 'access_note', NULL, 100, NOW())
ON CONFLICT (code) DO UPDATE SET
  scale = EXCLUDED.scale;

-- System accounts
INSERT INTO access_account (id, owner_user_id, account_type, currency_code, created_at)
VALUES
  ('treasury',     NULL, 'treasury',     'access_note', NOW()),
  ('rewards_pool', NULL, 'rewards_pool', 'access_note', NOW()),
  ('burn',         NULL, 'burn',         'access_note', NOW())
ON CONFLICT (id) DO NOTHING;

-- ----------------------------
-- USER WALLETS (one per user)
-- ----------------------------
INSERT INTO access_account (id, owner_user_id, account_type, currency_code, created_at)
SELECT
  md5(u.id || ':wallet'),
  u.id,
  'user_wallet',
  'access_note',
  NOW()
FROM "user" u
WHERE NOT EXISTS (
  SELECT 1
  FROM access_account a
  WHERE a.owner_user_id = u.id
    AND a.account_type = 'user_wallet'
);

-- ----------------------------
-- BALANCE ROWS (ensure exists for every account + AN asset)
-- ----------------------------
INSERT INTO access_balance (account_id, asset_id, balance, updated_at)
SELECT
  a.id,
  s.id AS asset_id,
  0,
  NOW()
FROM access_account a
JOIN access_asset s ON s.code = 'AN'
LEFT JOIN access_balance b
  ON b.account_id = a.id AND b.asset_id = s.id
WHERE b.account_id IS NULL;

COMMIT;

-- ----------------------------
-- SANITY CHECKS / REPORTS
-- ----------------------------

-- Wallet view
SELECT
  a.owner_user_id,
  u.name,
  u.email,
  b.balance,
  a.account_type,
  sub.status
FROM access_account a
JOIN access_balance b
  ON b.account_id = a.id
JOIN access_asset s
  ON s.id = b.asset_id AND s.code = 'AN'
JOIN "user" u
  ON u.id = a.owner_user_id
LEFT JOIN subscription sub
  ON sub.user_id = u.id
WHERE a.account_type = 'user_wallet'
ORDER BY u.email;

-- Quick checks
SELECT * FROM access_asset WHERE code='AN';
SELECT account_type, count(*) FROM access_account GROUP BY account_type ORDER BY account_type;
SELECT count(*) AS access_balance_rows FROM access_balance;

SELECT count(*) AS an_balance_rows
FROM access_balance b
JOIN access_asset s ON s.id=b.asset_id
WHERE s.code='AN';
