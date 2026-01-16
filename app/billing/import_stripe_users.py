# app/billing/import_stripe_users.py
from __future__ import annotations

import uuid
import stripe
from ..extensions import db
from ..models import User
from .stripe_client import _init_stripe


def stripe_import_users_command(limit: int = 100) -> None:
    """
    Import Stripe customers into our DB as Users (dev helper).
    We key by stripe_customer_id (unique) and prefer Stripe email when available.
    """
    _init_stripe()

    created = 0
    updated = 0
    skipped = 0

    # Stripe returns an auto-paging list; but limit is enough for dev
    customers = stripe.Customer.list(limit=limit)

    for cust in customers.data:
        cust_id = cust.get("id")
        if not cust_id:
            skipped += 1
            continue

        email = (cust.get("email") or "").strip().lower() or None
        name = cust.get("name") or None

        # Skip deleted customers
        if cust.get("deleted") is True:
            skipped += 1
            continue

        # If already imported by stripe_customer_id, update basic fields
        user = User.query.filter_by(stripe_customer_id=cust_id).first()
        if user:
            changed = False
            if email and user.email != email:
                # only update email if it doesn't collide
                collision = User.query.filter(User.email == email, User.id != user.id).first()
                if not collision:
                    user.email = email
                    changed = True
            if name and user.name != name:
                user.name = name
                changed = True
            if changed:
                updated += 1
            else:
                skipped += 1
            continue

        # If no email, we can still create a placeholder (dev)
        if not email:
            email = f"{cust_id}@stripe.local"

        # If email exists already for some other user, link Stripe customer to that user instead of creating dup
        existing_by_email = User.query.filter_by(email=email).first()
        if existing_by_email and not existing_by_email.stripe_customer_id:
            existing_by_email.stripe_customer_id = cust_id
            if name and not existing_by_email.name:
                existing_by_email.name = name
            updated += 1
            continue
        elif existing_by_email:
            # already claimed by another stripe customer -> skip to avoid corruption
            skipped += 1
            continue

        user = User(
            id=str(uuid.uuid4()),
            email=email,
            name=name,
            stripe_customer_id=cust_id,
            stripe_balance_cents=int(cust.get("balance") or 0),
        )
        db.session.add(user)
        created += 1

    db.session.commit()
    print(f"Stripe import complete. Created {created}, updated {updated}, skipped {skipped}.")
