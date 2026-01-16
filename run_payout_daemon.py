import time
from datetime import datetime
from app import create_app
from app.extensions import db
from app.models import Curriculum
from app.main.routes import _payout_curriculum_wallet  # yes, importing a helper is fine for now
from datetime import date
from app.models import User
from app.access_ledger.service import InsufficientFunds, AN_SCALE, \
    get_or_create_user_wallet, get_or_create_system_account, \
    get_an_asset, get_balance_ticks, post_access_txn, EntrySpec  # if this causes circular import, see note below
from app.access_ledger.service import charge_daily_tax_for_all_users

SLEEP_SECONDS = 60  # run once a minute

def main():
    app = create_app()
    with app.app_context():
        while True:
            try:
                # daily tax (safe to run every minute)
                day_utc = datetime.utcnow().date()
                tax_stats = charge_daily_tax_for_all_users(day_utc)
                print(f"[{datetime.utcnow().isoformat()}] daily tax stats={tax_stats}")

                # payouts
                curr_ids = [c.id for c in Curriculum.query.all()]
                total = 0
                for cid in curr_ids:
                    total += _payout_curriculum_wallet(cid)
                print(f"[{datetime.utcnow().isoformat()}] payout ticks distributed total={total}")

            except Exception as e:
                db.session.rollback()
                print("payout daemon error:", repr(e))

            time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
