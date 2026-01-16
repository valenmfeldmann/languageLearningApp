# app/billing/routes.py
import stripe
from flask import Blueprint
import uuid
from flask import request
from ..models import Subscription

from flask import abort
from .credits import grant_free_months
from datetime import datetime, timedelta, timezone
from flask import jsonify
from ..extensions import db
from ..models import User
from flask import render_template
from ..models import Plan
from .access import get_credit_balance_cents  # <-- adjust if name differs
from .pricing import buddy_discount_multiplier
from .credits import apply_unsubscribe_clawback_if_needed, reverse_unsubscribe_clawback_if_needed
from flask import current_app, redirect, url_for
from flask_login import login_required, current_user
import stripe

from .stripe_client import _init_stripe
from ..billing.stripe_client import ensure_stripe_customer  # wherever yours lives

bp = Blueprint("billing", __name__, url_prefix="/billing")

@bp.get("/pricing")
@login_required
def pricing():
    plans = (
        Plan.query
        .filter_by(active=True)
        .order_by(Plan.monthly_amount_cents.asc())
        .all()
    )

    buddy_count = int(getattr(current_user, "active_buddy_count", 0) or 0)
    mult = float(buddy_discount_multiplier(buddy_count))

    credit_balance_cents = int(get_credit_balance_cents(current_user.id) or 0)

    # --- NEW: access revoked banner (daily tax failure) ---
    sub = Subscription.query.filter_by(user_id=current_user.id).one_or_none()

    revoked_msg = None
    revoked_detail = None

    if sub and sub.access_revoked_reason == "daily_tax_insufficient":
        # Optional: show current AN balance for clarity
        from app.access_ledger.service import get_user_an_balance_ticks, AN_SCALE, DAILY_TAX_TICKS
        bal_ticks = get_user_an_balance_ticks(current_user.id)

        revoked_msg = "Access paused: you didn’t have enough AN to pay the daily access tax."
        revoked_detail = (
            f"Daily cost: {DAILY_TAX_TICKS / AN_SCALE:,.3f} AN. "
            f"Current balance: {bal_ticks / AN_SCALE:,.3f} AN."
        )

    plan_rows = []
    for p in plans:
        base = int(p.monthly_amount_cents)
        effective = int(round(base * mult))
        discount = max(0, base - effective)
        estimated_due = max(0, effective - credit_balance_cents)
        credits_applied_cents = min(credit_balance_cents, effective)

        plan_rows.append({
            "plan": p,
            "base_cents": base,
            "buddy_count": buddy_count,
            "multiplier": mult,
            "discount_cents": discount,
            "effective_cents": effective,
            "credit_balance_cents": credit_balance_cents,
            "estimated_due_cents": estimated_due,
            "credits_applied_cents": credits_applied_cents,
        })

    # return render_template("billing/pricing.html", plan_rows=plan_rows)
    return render_template("billing/pricing.html",
                           plan_rows=plan_rows,
                           revoked_msg=revoked_msg,
                           revoked_detail=revoked_detail)



@bp.post("/admin/comp_months/<int:months>")
@login_required
def admin_comp_months(months: int):
    # TODO: replace this with real admin auth later
    if not current_user.email.endswith("@gmail.com"):
        return ("forbidden", 403)

    u = User.query.get(current_user.id)
    now = datetime.now(timezone.utc)

    base = u.comped_until
    if base is None:
        base = now
    elif base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    u.comped_until = base + timedelta(days=30 * months)
    db.session.commit()

    return jsonify({"user_id": u.id, "comped_until": u.comped_until.isoformat()})



@bp.post("/admin/grant_free_months/<int:months>")
@login_required
def admin_grant_free_months(months: int):
    if current_user.email not in {"valen.summerresearch24@gmail.com", "valenmfeldmann@gmail.com"}:
        abort(403)

    res = grant_free_months(
        user=current_user,
        plan_code="base",
        months=months,
        reason="Charity reward (test)",
    )
    return jsonify(res.__dict__)




@bp.get("/checkout/<plan_code>")
@login_required
def checkout(plan_code: str):

    existing = (Subscription.query
                .filter_by(user_id=current_user.id)
                .filter(Subscription.status.in_(("active", "trialing", "past_due", "unpaid", "incomplete")))
                .first()
                )

    if existing:
        # Either redirect to billing portal, or just send them back
        return redirect("/app")

    plan = Plan.query.filter_by(code=plan_code, active=True).first()
    if not plan or not plan.stripe_price_id:
        abort(404)

    # Make sure the user has a Stripe customer
    ensure_stripe_customer(current_user)

    _init_stripe()

    success_url = url_for("billing.checkout_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}"
    cancel_url = url_for("billing.checkout_cancel", _external=True)

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=current_user.stripe_customer_id,
        line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=current_user.id,
        metadata={"user_id": current_user.id, "plan_code": plan.code},
        subscription_data={"metadata": {"user_id": current_user.id, "plan_code": plan.code}},
    )

    return redirect(session.url, code=303)


@bp.get("/success")
@login_required
def checkout_success():
    # Don’t update DB here. Webhook is the source of truth.
    return redirect("/app")


@bp.get("/cancel")
@login_required
def checkout_cancel():
    return redirect("/app")



def _ts_to_dt(ts: int | None):
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _upsert_subscription_from_stripe(sub, user_id: str, plan_code: str | None):
    # Resolve plan_id
    plan_id = None

    if plan_code:
        plan = Plan.query.filter_by(code=plan_code).first()
        if plan:
            plan_id = plan.id

    if plan_id is None:
        plan_id = _infer_plan_id_from_subscription(sub)

    row = Subscription.query.filter_by(user_id=user_id).first()
    if not row:
        row = Subscription(
            id=str(uuid.uuid4()),
            user_id=user_id,
            status=sub.get("status") or "unknown",
        )
        db.session.add(row)

    row.plan_id = plan_id
    row.stripe_subscription_id = sub.get("id")
    row.status = sub.get("status") or row.status
    row.current_period_end = _ts_to_dt(sub.get("current_period_end"))
    row.trial_end = _ts_to_dt(sub.get("trial_end"))
    row.cancel_at = _ts_to_dt(sub.get("cancel_at"))



def _update_user_stripe_balance_from_customer(customer_obj: dict):
    customer_id = customer_obj.get("id")
    if not customer_id:
        return

    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    if not user:
        return

    # Stripe convention: negative balance means credit
    user.stripe_balance_cents = int(customer_obj.get("balance") or 0)
    # db.session.commit()


def _infer_plan_id_from_subscription(sub: dict) -> int | None:
    """
    Fallback inference: match Stripe subscription's price id to Plan.stripe_price_id.
    """
    items = (sub.get("items") or {}).get("data") or []
    if not items:
        return None

    price = (items[0].get("price") or {})
    price_id = price.get("id")
    if not price_id:
        return None

    plan = Plan.query.filter_by(stripe_price_id=price_id).first()
    return plan.id if plan else None


# app/billing/routes.py (inside stripe_webhook)

@bp.post("/webhook")
def stripe_webhook():
    _init_stripe()

    payload = request.get_data(as_text=False)
    sig_header = request.headers.get("Stripe-Signature")

    whsec = current_app.config.get("STRIPE_WEBHOOK_SECRET")
    if not whsec:
        return ("STRIPE_WEBHOOK_SECRET is not set", 500)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, whsec)
    except ValueError:
        return ("Invalid payload", 400)
    except stripe.error.SignatureVerificationError:
        return ("Invalid signature", 400)

    etype = event["type"]
    data = event["data"]["object"]

    if etype == "checkout.session.completed":
        user_id = data.get("client_reference_id") or (data.get("metadata") or {}).get("user_id")
        plan_code = (data.get("metadata") or {}).get("plan_code")
        sub_id = data.get("subscription")
        if user_id and sub_id:
            sub = stripe.Subscription.retrieve(sub_id)
            _upsert_subscription_from_stripe(sub, user_id=user_id, plan_code=plan_code)

    elif etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        sub = data

        # resolve user_id
        user_id = (sub.get("metadata") or {}).get("user_id")
        plan_code = (sub.get("metadata") or {}).get("plan_code")

        if not user_id:
            customer_id = sub.get("customer")
            if customer_id:
                u = User.query.filter_by(stripe_customer_id=customer_id).first()
                if u:
                    user_id = u.id

        if not user_id:
            # db.session.commit()
            return ("ok", 200)

        _upsert_subscription_from_stripe(sub, user_id=user_id, plan_code=plan_code)

        event_id = event.get("id")

        if etype == "customer.subscription.updated":
            status = (sub.get("status") or "").lower()
            cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
            cancel_at = sub.get("cancel_at")
            is_scheduled_to_cancel = cancel_at_period_end or bool(cancel_at)

            if is_scheduled_to_cancel:
                anchor = str(cancel_at or "cancel_at_period_end")

                customer_id = sub.get("customer")
                if customer_id:
                    cust = stripe.Customer.retrieve(customer_id)
                    _update_user_stripe_balance_from_customer(cust)

                # apply_unsubscribe_clawback_if_needed(user_id, anchor=anchor)
                apply_unsubscribe_clawback_if_needed(user_id)

                if customer_id:
                    cust = stripe.Customer.retrieve(customer_id)
                    _update_user_stripe_balance_from_customer(cust)

            elif status in ("active", "trialing"):
                reverse_unsubscribe_clawback_if_needed(user_id)

                customer_id = sub.get("customer")
                if customer_id:
                    cust = stripe.Customer.retrieve(customer_id)
                    _update_user_stripe_balance_from_customer(cust)



    elif etype == "customer.updated":
        _update_user_stripe_balance_from_customer(data)

    elif etype in ("invoice.created", "invoice.finalized"):
        from .discounts import apply_buddy_cashback_for_invoice
        apply_buddy_cashback_for_invoice(data)

        customer_id = data.get("customer")
        if customer_id:
            cust = stripe.Customer.retrieve(customer_id)
            _update_user_stripe_balance_from_customer(cust)

    elif etype in ("invoice.paid", "invoice.payment_succeeded"):
        customer_id = data.get("customer")
        if customer_id:
            cust = stripe.Customer.retrieve(customer_id)
            _update_user_stripe_balance_from_customer(cust)

    db.session.commit()
    return ("ok", 200)



@bp.get("/subscribe")
@login_required
def subscribe():
    # simple start: list plans or just show one button
    return redirect(url_for("billing.pricing"))




@bp.get("/portal")
@login_required
def portal():
    _init_stripe()
    ensure_stripe_customer(current_user)

    return_url = url_for("main.app_home", _external=True)  # adjust endpoint if needed

    session = stripe.billing_portal.Session.create(
        customer=current_user.stripe_customer_id,
        return_url=return_url,
    )
    return redirect(session.url, code=303)
