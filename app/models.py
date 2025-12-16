from datetime import datetime
from .extensions import db
from flask_login import UserMixin

class User(UserMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.String, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=False)
    name = db.Column(db.String)
    image = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    stripe_customer_id = db.Column(db.String, unique=True)


# class User(db.Model):
#     __tablename__ = "user"
#     id = db.Column(db.String, primary_key=True)
#     email = db.Column(db.String, unique=True, nullable=False)
#     name = db.Column(db.String)
#     image = db.Column(db.String)
#     created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
#     stripe_customer_id = db.Column(db.String, unique=True)

class Subscription(db.Model):
    __tablename__ = "subscription"
    id = db.Column(db.String, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey("user.id"), unique=True, nullable=False)

    stripe_subscription_id = db.Column(db.String, unique=True)
    status = db.Column(db.String, nullable=False)  # trialing, active, canceled...
    current_period_end = db.Column(db.DateTime)
    trial_end = db.Column(db.DateTime)
    cancel_at_period_end = db.Column(db.Boolean, default=False, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class BillingConfig(db.Model):
    __tablename__ = "billing_config"
    id = db.Column(db.Integer, primary_key=True, default=1)
    trial_days = db.Column(db.Integer, default=7, nullable=False)
    stripe_price_id = db.Column(db.String, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
