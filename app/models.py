from flask_login import UserMixin
import uuid

from sqlalchemy import Index, text

from .extensions import db
from datetime import datetime, date



def _uuid() -> str:
    return uuid.uuid4().hex


class LessonAsset(db.Model):
    __tablename__ = "lesson_asset"

    id = db.Column(db.String, primary_key=True, default=_uuid)
    lesson_id = db.Column(db.String, db.ForeignKey("lesson.id", ondelete="CASCADE"), nullable=False, index=True)

    # reference used inside lesson.json blocks, e.g. "assets/img1.png"
    ref = db.Column(db.String, nullable=False)
    storage_path = db.Column(db.String, nullable=False)  # where you actually saved it
    content_type = db.Column(db.String, nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("lesson_id", "ref", name="uq_lesson_asset_ref"),
    )



class LessonBlock(db.Model):
    __tablename__ = "lesson_block"

    id = db.Column(db.String, primary_key=True, default=_uuid)
    lesson_id = db.Column(db.String, db.ForeignKey("lesson.id", ondelete="CASCADE"), nullable=False, index=True)

    position = db.Column(db.Integer, nullable=False)  # 0,1,2,...
    type = db.Column(db.String, nullable=False, index=True)

    payload_json = db.Column(db.JSON, nullable=False, default=dict)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("lesson_id", "position", name="uq_lesson_block_position"),
    )

class LessonBlockProgress(db.Model):
    __tablename__ = "lesson_block_progress"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.String, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_block_id = db.Column(db.String, db.ForeignKey("lesson_block.id", ondelete="CASCADE"), nullable=False, index=True)

    # quiz-only for now
    last_choice_index = db.Column(db.Integer, nullable=True)
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    answered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # future-proofing for time-based currency / anti-gaming (optional now)
    seconds_spent_total = db.Column(db.Integer, nullable=False, default=0)
    last_seen_at = db.Column(db.DateTime, nullable=True)

    attempt_id = db.Column(db.String, db.ForeignKey("lesson_attempt.id", ondelete="CASCADE"), nullable=False,
                           index=True)

    __table_args__ = (
        db.UniqueConstraint("attempt_id", "lesson_block_id", name="uq_attempt_block_progress"),
    )





class Lesson(db.Model):
    __tablename__ = "lesson"

    id = db.Column(db.String, primary_key=True, default=_uuid)
    code = db.Column(db.String, unique=True, nullable=False, index=True)  # e.g. "spanish-basics-01"
    title = db.Column(db.String, nullable=False)
    description = db.Column(db.Text, nullable=True)

    # optional but useful
    language_code = db.Column(db.String, nullable=True, index=True)  # "es", "fr", etc.
    created_by_user_id = db.Column(db.String, db.ForeignKey("user.id"), nullable=True, index=True)

    # Visibility for lesson discovery/access (v1)
    # - private: only creator can see/use (default)
    # - public: discoverable and usable by anyone
    # (later you can add "unlisted" + lesson_access invitations without breaking this)
    visibility = db.Column(db.String, nullable=False, default="private", index=True)

    # Keep existing published fields if you want them for “content is finalized”
    # (published != public; you can later decide how they interact)
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    published_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    subject_code = db.Column(
        db.String,
        db.ForeignKey("lesson_subject.code", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )



class LessonCompletion(db.Model):
    __tablename__ = "lesson_completion"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.String, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id = db.Column(db.String, db.ForeignKey("lesson.id", ondelete="CASCADE"), nullable=False, index=True)

    attempt_id = db.Column(
        db.String,
        db.ForeignKey("lesson_attempt.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    completed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    curriculum_pos = db.Column(db.Integer, nullable=True, index=True)

    __table_args__ = (
        db.Index("ix_completion_user_lesson_time", "user_id", "lesson_id", "completed_at"),
    )


class LessonSubject(db.Model):
    __tablename__ = "lesson_subject"

    # short stable id like "math", "spanish_vocab", "us_history"
    code = db.Column(db.String, primary_key=True)
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.Text, nullable=True)

    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class LessonSchool(db.Model):
    __tablename__ = "lesson_school"

    # short stable id like "hope", "umich", "hs_ap", "mit_ocw"
    code = db.Column(db.String, primary_key=True)
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.Text, nullable=True)

    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class TriviaAnswer(db.Model):
    __tablename__ = "trivia_answer"

    id = db.Column(db.BigInteger, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user_id = db.Column(db.String, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_block_id = db.Column(db.String, db.ForeignKey("lesson_block.id", ondelete="CASCADE"), nullable=False, index=True)

    chosen_index = db.Column(db.Integer, nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False)

    payout_ticks = db.Column(db.BigInteger, nullable=False, default=0)

    # what filter the user used (optional but useful)
    subject_code = db.Column(db.String, db.ForeignKey("lesson_subject.code", ondelete="SET NULL"), nullable=True, index=True)
    search_text = db.Column(db.String, nullable=True)


class TriviaBadVote(db.Model):
    __tablename__ = "trivia_bad_vote"

    id = db.Column(db.BigInteger, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user_id = db.Column(db.String, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_block_id = db.Column(db.String, db.ForeignKey("lesson_block.id", ondelete="CASCADE"), nullable=False, index=True)

    subject_code = db.Column(db.String, db.ForeignKey("lesson_subject.code", ondelete="SET NULL"), nullable=True, index=True)

    __table_args__ = (
        db.UniqueConstraint("user_id", "lesson_block_id", name="uq_bad_vote_user_block"),
    )



class AnalyticsEvent(db.Model):
    __tablename__ = "analytics_event"

    id = db.Column(db.BigInteger, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user_id = db.Column(db.String, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)

    event_type = db.Column(db.String, nullable=False, index=True)     # e.g. curriculum_view, lesson_view, search_query, search_click
    entity_type = db.Column(db.String, nullable=True, index=True)     # curriculum, lesson, etc
    entity_id = db.Column(db.String, nullable=True, index=True)

    # freeform event payload (query string, position, referrer, etc)
    props_json = db.Column(db.JSON, nullable=True)



class LessonAttempt(db.Model):
    __tablename__ = "lesson_attempt"

    id = db.Column(db.String, primary_key=True, default=_uuid)

    user_id = db.Column(db.String, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id = db.Column(db.String, db.ForeignKey("lesson.id", ondelete="CASCADE"), nullable=False, index=True)

    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True, index=True)

    seconds_spent_total = db.Column(db.Integer, nullable=False, default=0)
    last_heartbeat_at = db.Column(db.DateTime, nullable=True)

    curriculum_id = db.Column(
        db.String,
        db.ForeignKey("curriculum.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    curriculum_pos = db.Column(db.Integer, nullable=True, index=True)

    # Optional but very useful later:
    # schedule_item_id = db.Column(db.String, db.ForeignKey("scheduled_lesson.id", ondelete="CASCADE"), nullable=True, index=True)


class CurriculumDayActivity(db.Model):
    __tablename__ = "curriculum_day_activity"

    id = db.Column(db.BigInteger, primary_key=True)
    curriculum_id = db.Column(db.String, db.ForeignKey("curriculum.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.String, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)

    day = db.Column(db.Date, nullable=False, index=True)   # UTC day (simple + consistent)
    seconds_spent_total = db.Column(db.Integer, nullable=False, default=0)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("curriculum_id", "user_id", "day", name="uq_curr_day_user"),
        db.Index("ix_curr_day_curr_day", "curriculum_id", "day"),
    )



class AccessAccount(db.Model):
    __tablename__ = "access_account"

    id = db.Column(db.String, primary_key=True, default=_uuid)
    owner_user_id = db.Column(db.String, db.ForeignKey("user.id"), nullable=True, index=True)

    # examples: "user_wallet", "treasury", "rewards_pool", "burn"
    account_type = db.Column(db.String, nullable=False, index=True)

    # keep room for future currencies, but you can hardcode to "access_note" for now
    currency_code = db.Column(db.String, nullable=False, default="access_note", index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class AccessTxn(db.Model):
    __tablename__ = "access_txn"

    id = db.Column(db.String, primary_key=True, default=_uuid)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    event_type = db.Column(db.String, nullable=False, index=True)
    actor_user_id = db.Column(db.String, db.ForeignKey("user.id"), nullable=True, index=True)

    context_type = db.Column(db.String, nullable=True, index=True)
    context_id = db.Column(db.String, nullable=True, index=True)

    # critical for retries / double clicks / job reruns
    idempotency_key = db.Column(db.String, unique=True, nullable=False, index=True)

    memo_json = db.Column(db.JSON, nullable=True)


class AccessEntry(db.Model):
    __tablename__ = "access_entry"

    id = db.Column(db.String, primary_key=True, default=_uuid)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    txn_id = db.Column(db.String, db.ForeignKey("access_txn.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.String, db.ForeignKey("access_account.id", ondelete="RESTRICT"), nullable=False, index=True)

    asset_id = db.Column(db.String, db.ForeignKey("access_asset.id", ondelete="RESTRICT"), nullable=False, index=True)

    # signed integer, smallest unit in that asset’s scale
    delta = db.Column(db.BigInteger, nullable=False)

    entry_type = db.Column(db.String, nullable=False, default="principal", index=True)

    __table_args__ = (
        db.Index("ix_access_entry_account_asset", "account_id", "asset_id"),
    )


class AccessBalance(db.Model):
    __tablename__ = "access_balance"

    account_id = db.Column(db.String, db.ForeignKey("access_account.id", ondelete="CASCADE"), primary_key=True)
    asset_id = db.Column(db.String, db.ForeignKey("access_asset.id", ondelete="CASCADE"), primary_key=True)

    balance = db.Column(db.BigInteger, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.Index("ix_access_balance_account_asset", "account_id", "asset_id"),
    )




class User(UserMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.String, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=False)
    name = db.Column(db.String)
    image = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    stripe_customer_id = db.Column(db.String, unique=True)
    comped_until = db.Column(db.DateTime, nullable=True)
    active_buddy_count = db.Column(db.Integer, default=0, nullable=False)
    stripe_balance_cents = db.Column(db.Integer, default=0, nullable=False)

    # NEW: cancel clawback tracking
    unsubscribe_clawback_pending_cents = db.Column(db.Integer, default=0, nullable=False)
    unsubscribe_clawback_pending_txn_id = db.Column(db.String, nullable=True, unique=True)

    access_level_mult = db.Column(db.Float, nullable=False, default=1.0)



class Plan(db.Model):
    __tablename__ = "plan"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String, unique=True, nullable=False)
    stripe_price_id = db.Column(db.String, nullable=False)
    monthly_amount_cents = db.Column(db.Integer, nullable=False)  # NEW
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Subscription(db.Model):
    __tablename__ = "subscription"
    id = db.Column(db.String, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey("user.id"), unique=True, nullable=False)

    plan_id = db.Column(db.Integer, db.ForeignKey("plan.id"), nullable=True)
    stripe_subscription_id = db.Column(db.String, unique=True)

    status = db.Column(db.String, nullable=False)  # trialing, active, canceled...

    trial_end = db.Column(db.DateTime)
    cancel_at_period_end = db.Column(db.Boolean, default=False, nullable=False)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    cancel_at = db.Column(db.DateTime, nullable=True)  # NEW

    # NEW: why access was revoked (e.g. daily tax failure)
    access_revoked_reason = db.Column(db.String(255), nullable=True)
    access_revoked_anchor = db.Column(db.String(255), nullable=True)
    access_revoked_at = db.Column(db.DateTime, nullable=True)



# app/models.py

# app/models.py

class BillingConfig(db.Model):
    __tablename__ = "billing_config"
    id = db.Column(db.Integer, primary_key=True, default=1)
    trial_days = db.Column(db.Integer, default=7, nullable=False)
    stripe_price_id = db.Column(db.String, nullable=False)
    unsubscribe_credit_clawback_rate = db.Column(db.Float, default=0.5, nullable=False)  # NEW
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BillingCreditGrant(db.Model):
    __tablename__ = "billing_credit_grant"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.String,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    months = db.Column(db.Integer, nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)

    reason = db.Column(db.Text, nullable=False)

    stripe_balance_txn_id = db.Column(db.String, unique=True, nullable=False)

    # NEW: promo vs user-funded, and clawback tracking
    is_promo = db.Column(db.Boolean, default=True, nullable=False)
    clawback_eligible = db.Column(db.Boolean, default=True, nullable=False)

    clawed_back_cents = db.Column(db.Integer, default=0, nullable=False)
    clawed_back_at = db.Column(db.DateTime, nullable=True)
    clawback_stripe_balance_txn_id = db.Column(db.String, unique=True, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)




class BuddyLink(db.Model):
    __tablename__ = "buddy_link"

    id = db.Column(db.Integer, primary_key=True)

    requester_id = db.Column(db.String, db.ForeignKey("user.id"), nullable=False, index=True)
    addressee_id = db.Column(db.String, db.ForeignKey("user.id"), nullable=False, index=True)

    status = db.Column(db.String, nullable=False, default="pending", index=True)  # pending, accepted, rejected, ended
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    accepted_at = db.Column(db.DateTime)
    ended_at = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint("requester_id", "addressee_id", name="uq_buddy_pair"),
        db.CheckConstraint("requester_id <> addressee_id", name="ck_no_self_buddy"),
    )



class TransactionTax(db.Model):
    __tablename__ = "transaction_tax"

    id = db.Column(db.Integer, primary_key=True)
    transaction_type = db.Column(db.String, unique=True, nullable=False)
    tax_rate = db.Column(db.Float, nullable=False)  # e.g. 0.05 = 5%
    max_amount = db.Column(db.Integer)  # optional cap
    enabled = db.Column(db.Boolean, default=True, nullable=False)




class Curriculum(db.Model):
    __tablename__ = "curriculum"
    id = db.Column(db.String, primary_key=True, default=_uuid)
    code = db.Column(db.String, nullable=False, unique=True, index=True)
    title = db.Column(db.String, nullable=False)
    description = db.Column(db.Text, nullable=True)

    created_by_user_id = db.Column(db.String, db.ForeignKey("user.id"), nullable=True)

    # NEW: curriculum earnings wallet (Access Notes)
    wallet_account_id = db.Column(
        db.String,
        db.ForeignKey("access_account.id", ondelete="RESTRICT"),
        nullable=True,          # start nullable so migration/backfill is easy
        unique=True,
        index=True,
    )

    is_published = db.Column(db.Boolean, nullable=False, default=False)
    published_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    subject_code = db.Column(
        db.String,
        db.ForeignKey("lesson_subject.code", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    school_code = db.Column(
        db.String,
        db.ForeignKey("lesson_school.code", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    cover_image_url = db.Column(db.Text, nullable=True)


class CurriculumOwner(db.Model):
    __tablename__ = "curriculum_owner"

    id = db.Column(db.Integer, primary_key=True)

    curriculum_id = db.Column(
        db.String,
        db.ForeignKey("curriculum.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.String,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # payout weight
    shares = db.Column(db.Integer, nullable=False, default=0)

    # permissions (independent of shares)
    can_view_analytics = db.Column(db.Boolean, nullable=False, default=False)
    can_edit = db.Column(db.Boolean, nullable=False, default=False)
    can_manage_ownership = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("curriculum_id", "user_id", name="uq_curr_owner"),
        db.CheckConstraint("shares >= 0", name="ck_curr_owner_shares_nonneg"),
        db.Index("ix_curr_owner_curr", "curriculum_id"),
        db.Index("ix_curr_owner_user", "user_id"),
    )


# Not done yet: a simple /author/curriculum/<id>/owners page where an admin can:
# search user by email
# transfer shares
# toggle can_edit / can_view_analytics
# We can do that next once the migration is applied.





class AccessAsset(db.Model):
    __tablename__ = "access_asset"

    id = db.Column(db.String, primary_key=True, default=_uuid)

    code = db.Column(db.String, nullable=False)  # <-- remove unique=True, index=True

    asset_type = db.Column(db.String, nullable=False, index=True)
    curriculum_id = db.Column(
        db.String,
        db.ForeignKey("curriculum.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    scale = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ux_access_asset_code", "code", unique=True),
        Index(
            "ux_access_asset_curriculum_share",
            "curriculum_id",
            unique=True,
            postgresql_where=text("asset_type = 'curriculum_share'"),
        ),
    )



class CurriculumItem(db.Model):
    __tablename__ = "curriculum_item"
    id = db.Column(db.String, primary_key=True, default=_uuid)

    curriculum_id = db.Column(db.String, db.ForeignKey("curriculum.id", ondelete="CASCADE"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False)

    # "phase" or "lesson"
    item_type = db.Column(db.String, nullable=False)

    # for phases
    phase_title = db.Column(db.String, nullable=True)

    # for lesson items
    lesson_id = db.Column(db.String, db.ForeignKey("lesson.id", ondelete="CASCADE"), nullable=True, index=True)

    # optional note (shown under item)
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("curriculum_id", "position", name="uq_curriculum_item_position"),
    )



class MarketOrder(db.Model):
    __tablename__ = "market_order"

    id = db.Column(db.Integer, primary_key=True)

    curriculum_id = db.Column(db.String, db.ForeignKey("curriculum.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.String, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)

    side = db.Column(db.String, nullable=False, index=True)   # "bid" or "ask"
    status = db.Column(db.String, nullable=False, index=True, default="open")  # open, canceled, filled, partial

    # price in AN ticks per 1 share (since 1 AN = 1000 ticks)
    price_ticks_per_share = db.Column(db.BigInteger, nullable=False)

    qty_shares = db.Column(db.Integer, nullable=False)
    remaining_shares = db.Column(db.Integer, nullable=False)

    # locked amounts sitting in escrow (so cancel is deterministic)
    locked_an_ticks = db.Column(db.BigInteger, nullable=False, default=0)
    locked_shares = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    canceled_at = db.Column(db.DateTime, nullable=True)
    filled_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint("side in ('bid','ask')", name="ck_market_order_side"),
        db.CheckConstraint("status in ('open','canceled','filled','partial')", name="ck_market_order_status"),
        db.CheckConstraint("price_ticks_per_share > 0", name="ck_market_order_price_pos"),
        db.CheckConstraint("qty_shares > 0", name="ck_market_order_qty_pos"),
        db.CheckConstraint("remaining_shares >= 0", name="ck_market_order_rem_nonneg"),
    )


class MarketTrade(db.Model):
    __tablename__ = "market_trade"

    id = db.Column(db.Integer, primary_key=True)

    curriculum_id = db.Column(db.String, db.ForeignKey("curriculum.id", ondelete="CASCADE"), nullable=False, index=True)

    buy_order_id = db.Column(db.Integer, db.ForeignKey("market_order.id", ondelete="SET NULL"), nullable=True, index=True)
    sell_order_id = db.Column(db.Integer, db.ForeignKey("market_order.id", ondelete="SET NULL"), nullable=True, index=True)

    buyer_id = db.Column(db.String, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)
    seller_id = db.Column(db.String, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)

    price_ticks_per_share = db.Column(db.BigInteger, nullable=False)
    qty_shares = db.Column(db.Integer, nullable=False)

    ledger_txn_id = db.Column(db.String, db.ForeignKey("access_txn.id", ondelete="SET NULL"), nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.CheckConstraint("price_ticks_per_share > 0", name="ck_market_trade_price_pos"),
        db.CheckConstraint("qty_shares > 0", name="ck_market_trade_qty_pos"),
    )

class CurriculumOrder(db.Model):
    __tablename__ = "curriculum_order"

    id = db.Column(db.String, primary_key=True, default=_uuid)

    curriculum_id = db.Column(db.String, db.ForeignKey("curriculum.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.String, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)

    # "bid" = buy shares (pays AN), "ask" = sell shares (receives AN)
    side = db.Column(db.String, nullable=False, index=True)

    # price in AN ticks per 1 share (since AN_SCALE=1000, price_ticks=1500 means 1.5 AN/share)
    price_ticks = db.Column(db.BigInteger, nullable=False)

    qty_initial = db.Column(db.Integer, nullable=False)
    qty_remaining = db.Column(db.Integer, nullable=False)

    status = db.Column(db.String, nullable=False, default="open", index=True)  # open, filled, canceled

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    canceled_at = db.Column(db.DateTime, nullable=True)
    filled_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint("side in ('bid','ask')", name="ck_order_side"),
        db.CheckConstraint("price_ticks > 0", name="ck_order_price_pos"),
        db.CheckConstraint("qty_initial > 0", name="ck_order_qty_initial_pos"),
        db.CheckConstraint("qty_remaining >= 0", name="ck_order_qty_remaining_nonneg"),
        db.Index("ix_order_book", "curriculum_id", "side", "status", "price_ticks", "created_at"),
    )


