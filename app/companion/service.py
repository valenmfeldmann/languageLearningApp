import random
import os
import uuid
from flask import current_app
from ..models import Plant, Garden, AccessAccount
from ..access_ledger.service import post_access_txn, EntrySpec, get_an_asset
from datetime import datetime
from ..extensions import db
from ..models import Character



def create_new_dog(user):
    """Pulls a random name and breed to initialize a new companion."""
    names_path = os.path.join('instance', 'character_names.txt')

    # Load gender-neutral names
    if os.path.exists(names_path):
        with open(names_path, 'r') as f:
            names = [n.strip() for n in f.readlines() if n.strip()]
    else:
        names = ["Buddy", "Pip", "Lucky"]

    # Assign random breed
    breeds = ['golden', 'pug', 'corgi', 'husky', 'shiba']

    dog = Character(
        user_id=user.id,
        name=random.choice(names),
        breed_type="placeholder", #random.choice(breeds),
        is_alive=True,
        death_acknowledged=True  # A new dog doesn't need its death acknowledged yet!
    )
    db.session.add(dog)
    db.session.commit()
    return dog




# def buy_seed_for_dog(user, plant_slug, cost_ticks):
#     dog = Character.query.filter_by(user_id=user.id, is_alive=True).first()
#     if not dog:
#         return False, "You don't have a companion to give seeds to!"
#
#     an_asset = get_an_asset()
#
#
#     # 1. FIND THE TREASURY ACCOUNT
#     # We look for the system account that handles the "access_note" currency
#     treasury = AccessAccount.query.filter_by(
#         account_type="treasury",
#         currency_code="access_note"
#     ).first()
#
#     if not treasury:
#         current_app.logger.error("Treasury account not found!")
#         return False, "The shop is closed (System Account Error)."
#
#     an_asset = get_an_asset()
#     idem_key = f"seed_purchase_{user.id}_{uuid.uuid4().hex[:12]}"
#
#     # 1. Find the User's Wallet Account
#     user_wallet = AccessAccount.query.filter_by(
#         owner_user_id=user.id,
#         account_type="user_wallet",
#         currency_code="access_note"
#     ).first()
#
#     if not user_wallet:
#         return False, "User wallet not found."
#
#     # 2. Update the Ledger Transaction call
#     try:
#         post_access_txn(
#             event_type="seed_purchase",
#             idempotency_key=idem_key,
#             actor_user_id=user.id,
#             forbid_user_overdraft=True,
#             entries=[
#                 # Debit the user wallet (delta is negative)
#                 EntrySpec(
#                     account_id=user_wallet.id,  # Use the actual account ID
#                     asset_id=an_asset.id,
#                     delta=-cost_ticks,
#                 ),
#                 # Credit the treasury account (delta is positive)
#                 EntrySpec(
#                     account_id=treasury.id,
#                     asset_id=an_asset.id,
#                     delta=cost_ticks,
#                 ),
#             ],
#             memo_json={"plant_slug": plant_slug, "dog_name": dog.name}
#         )
#     except Exception as e:
#         current_app.logger.error(f"Ledger txn failed: {e}")
#         return False, f"Transaction failed: {str(e)}"
#
#     # 3. Update the Garden (Database)
#     plant = Plant.query.filter_by(identifier=plant_slug).first()
#     if not plant:
#         plant = Plant(name=plant_slug.replace('_', ' ').title(), identifier=plant_slug)
#         db.session.add(plant)
#         db.session.commit()
#
#     garden_entry = Garden.query.filter_by(character_id=dog.id, plant_id=plant.id).first()
#     if garden_entry:
#         if garden_entry.level < 5:  # Cap at your max asset level
#             garden_entry.level += 1
#             msg = f"{dog.name} is thrilled! The {plant.name} is now Level {garden_entry.level}."
#         else:
#             msg = f"The {plant.name} is already maxed out at level {garden_entry.level}."
#     else:
#         new_entry = Garden(character_id=dog.id, plant_id=plant.id, level=1)
#         db.session.add(new_entry)
#         msg = f"A new {plant.name} has sprouted for {dog.name}!"
#
#     db.session.commit()
#     return True, msg


def buy_seed_for_dog(user, plant_slug, cost_ticks):
    dog = Character.query.filter_by(user_id=user.id, is_alive=True).first()
    if not dog:
        return False, "You don't have a companion to give seeds to!"

    # 1. PRE-CHECK: Find the plant and the garden entry FIRST
    plant = Plant.query.filter_by(identifier=plant_slug).first()
    if not plant:
        # Create the plant catalog entry if it doesn't exist
        plant = Plant(name=plant_slug.replace('_', ' ').title(), identifier=plant_slug)
        db.session.add(plant)
        db.session.commit()

    garden_entry = Garden.query.filter_by(character_id=dog.id, plant_id=plant.id).first()

    # 2. GUARD CLAUSE: Stop if already at max level
    if garden_entry and garden_entry.level >= 5:
        return False, f"The {plant.name} is already at its maximum level!"

    # 3. LEDGER PREPARATION
    treasury = AccessAccount.query.filter_by(
        account_type="treasury",
        currency_code="access_note"
    ).first()

    if not treasury:
        current_app.logger.error("Treasury account not found!")
        return False, "The shop is closed (System Account Error)."

    user_wallet = AccessAccount.query.filter_by(
        owner_user_id=user.id,
        account_type="user_wallet",
        currency_code="access_note"
    ).first()

    if not user_wallet:
        return False, "User wallet not found."

    an_asset = get_an_asset()
    idem_key = f"seed_purchase_{user.id}_{uuid.uuid4().hex[:12]}"

    # 4. LEDGER TRANSACTION (Only happens if not maxed out)
    try:
        post_access_txn(
            event_type="seed_purchase",
            idempotency_key=idem_key,
            actor_user_id=user.id,
            forbid_user_overdraft=True,
            entries=[
                EntrySpec(
                    account_id=user_wallet.id,
                    asset_id=an_asset.id,
                    delta=-cost_ticks,
                ),
                EntrySpec(
                    account_id=treasury.id,
                    asset_id=an_asset.id,
                    delta=cost_ticks,
                ),
            ],
            memo_json={"plant_slug": plant_slug, "dog_name": dog.name}
        )
    except Exception as e:
        current_app.logger.error(f"Ledger txn failed: {e}")
        return False, f"Transaction failed: {str(e)}"

    # 5. UPDATE DATABASE
    if garden_entry:
        garden_entry.level += 1
        msg = f"{dog.name} is thrilled! The {plant.name} is now Level {garden_entry.level}."
    else:
        new_entry = Garden(character_id=dog.id, plant_id=plant.id, level=1)
        db.session.add(new_entry)
        msg = f"A new {plant.name} has sprouted for {dog.name}!"

    db.session.commit()
    return True, msg



# def check_companion_status(user, current_balance):
#     """Checks if the dog survives based on the current AN balance."""
#     dog = Character.query.filter_by(user_id=user.id, is_alive=True).first()
#
#     if not dog:
#         return
#
#     # If balance hits zero, the companion passes away
#     if current_balance <= 0:
#         dog.is_alive = False
#         dog.died_at = datetime.utcnow()
#         db.session.commit()
#         return True  # Indicates a death event occurred
#
#     return False



def check_for_companion_succession(user):
    """
    Finds the most recent unacknowledged death.
    Crucially, we DON'T flip the flag here yet.
    """
    unacknowledged_death = Character.query.filter_by(
        user_id=user.id,
        is_alive=False,
        death_acknowledged=False
    ).order_by(Character.died_at.desc()).first()

    live_dog = Character.query.filter_by(user_id=user.id, is_alive=True).first()

    # If a death happened but no new dog was birthed yet (e.g. Scout's failure)
    if unacknowledged_death and not live_dog:
        live_dog = create_new_dog(user)
        # Note: create_new_dog should set death_acknowledged=True for the NEW dog
        db.session.commit()

    return unacknowledged_death, live_dog


def handle_companion_transition(user_id):
    """Marks the current companion as dead and records the time."""
    old_dog = Character.query.filter_by(user_id=user_id, is_alive=True).first()

    if old_dog:
        old_dog.is_alive = False
        old_dog.died_at = datetime.utcnow()  # This triggers the "Big Deal" memorial
        db.session.commit()
        return True

    return False


def get_companion_message(dog_name, expression, is_transition, old_dog_name=None, user_name="Learner"):
    if is_transition:
        return [
            f"I've taken over for {old_dog_name}. I'm ready to help you get things back on track. I believe in you, {user_name}!",
            f"Hi {user_name}! {old_dog_name} told me all about you. Let's make today a great study day!",
        ]

    if expression == 'pain':
        return [
            "Whimper... I'm feeling a bit weak. Could we get some AN soon?",
            "It's getting a bit cold in here... maybe some studying would help?",
            "I'm rooting for you, but I'm losing my strength. Let's hit the books!"
        ]

    # Default 'happy' messages
    return [
        "Bark! Welcome back! I'm happy to see you again!",
        f"I was just thinking about you, {user_name}! Ready to learn?",
        "Everything looks great in the sanctuary. You're doing amazing!",
        "Tail wags! I feel so energized when you're around."
    ]