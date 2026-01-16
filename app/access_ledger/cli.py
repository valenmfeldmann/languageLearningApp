# app/access_ledger/cli.py
import uuid
from flask import current_app
from app.extensions import db
from app.models import User, AccessBalance
from app.access_ledger.service import (
    get_or_create_system_account,
    get_or_create_user_wallet,
    post_access_txn,
    EntrySpec, get_an_asset,
)
from datetime import date


def register_cli(app):
    @app.cli.command("ledger-smoketest")
    def ledger_smoketest():
        u = User.query.first()
        if not u:
            raise RuntimeError("No users in DB. Log in once to create a user row.")

        wallet = get_or_create_user_wallet(u.id)
        rewards = get_or_create_system_account("rewards_pool")


        key = f"smoketest:{u.id}:mint10"  # stable on purpose

        txn_id = post_access_txn(
            event_type="admin_mint",
            idempotency_key=key,
            actor_user_id=u.id,
            entries=[
                EntrySpec(account_id=rewards.id, delta=-10, entry_type="mint"),
                EntrySpec(account_id=wallet.id, delta=+10, entry_type="mint"),
            ],
            memo_json={"note": "ledger smoketest"},
        )

        an = get_an_asset()
        bal = AccessBalance.query.filter_by(account_id=wallet.id, asset_id=an.id).one_or_none()

        # bal = AccessBalance.query.get(wallet.id).balance
        current_app.logger.info("Smoketest txn_id=%s wallet_balance=%s", txn_id, bal)

        # Run again to confirm idempotency
        txn_id2 = post_access_txn(
            event_type="admin_mint",
            idempotency_key=key,
            actor_user_id=u.id,
            entries=[
                EntrySpec(account_id=rewards.id, delta=-10, entry_type="mint"),
                EntrySpec(account_id=wallet.id, delta=+10, entry_type="mint"),
            ],
        )
        bal2 = AccessBalance.query.get(wallet.id).balance
        current_app.logger.info("Idempotency txn_id2=%s wallet_balance2=%s", txn_id2, bal2)

        if txn_id != txn_id2:
            raise RuntimeError("Idempotency failed: txn ids differ")
        if bal2 != bal:
            raise RuntimeError("Idempotency failed: balance changed on retry")

        print("OK: ledger smoketest passed")
