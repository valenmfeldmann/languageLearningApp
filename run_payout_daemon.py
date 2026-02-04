import time
import pytz
from datetime import datetime
from app import create_app
from app.extensions import db
from app.models import Curriculum
from app.main.routes import _payout_curriculum_wallet
from app.access_ledger.service import charge_daily_tax_for_all_users

SLEEP_SECONDS = 60


def main():
    app = create_app()
    central = pytz.timezone('US/Central')

    with app.app_context():
        while True:
            try:
                # 1. Get current time in Central
                now_central = datetime.now(central)

                # 2. Only run tax and payouts during the first minute of midnight Central
                if now_central.hour == 0 and now_central.minute == 0:
                    print(f"[{now_central.isoformat()}] Starting midnight run...")

                    # Use UTC date for the idempotency key to stay consistent with your ledger
                    day_utc = datetime.utcnow().date()

                    # Charge Daily Tax
                    tax_stats = charge_daily_tax_for_all_users(day_utc)
                    print(f"Daily tax stats={tax_stats}")

                    # Distribute Payouts
                    curr_ids = [c.id for c in Curriculum.query.all()]
                    total = 0
                    for cid in curr_ids:
                        total += _payout_curriculum_wallet(cid)
                    print(f"Payout ticks distributed total={total}")

                    # Sleep for 61 seconds to ensure we don't trigger twice in the same minute
                    time.sleep(61)
                    continue

            except Exception as e:
                db.session.rollback()
                print("payout daemon error:", repr(e))

            time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()