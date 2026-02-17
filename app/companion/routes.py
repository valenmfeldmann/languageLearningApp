import random

from app.access_ledger.service import get_balance_ticks, get_an_asset  # Adjust based on your actual service
import os
from flask import Blueprint, send_from_directory, current_app, flash, redirect, url_for, request
from flask import render_template
from flask_login import login_required, current_user

from .service import buy_seed_for_dog, check_for_companion_succession, get_companion_message
from .. import db
from ..models import Character, Garden
import mimetypes




bp = Blueprint("companion", __name__, url_prefix="/companion")



import mimetypes

import mimetypes


@bp.get("/assets/dog/<breed>/<expression>")
def dog_asset(breed, expression):
    breed_dir = os.path.join(current_app.static_folder, 'assets', 'characters', breed)

    # Priority logic
    extensions = ['png', 'jpg', 'jpeg', 'gif'] if expression == "favicon" else ['mp4', 'webm', 'gif', 'png']

    for ext in extensions:
        filename = f"{expression}.{ext}"
        full_path = os.path.join(breed_dir, filename)

        if os.path.exists(full_path):
            mime_type, _ = mimetypes.guess_type(full_path)
            print(f">>> SERVING: {breed}/{filename} AS {mime_type}")  # This shows in your terminal
            return send_from_directory(breed_dir, filename, mimetype=mime_type)

    # Log the failure before the fallback
    print(f"!!! FAILED: Could not find {expression} for {breed} in {breed_dir}")
    placeholder_dir = os.path.join(current_app.static_folder, 'assets', 'characters')
    return send_from_directory(placeholder_dir, "placeholder.png")



@bp.get("/assets/plant/<identifier>/<int:level>")
def plant_asset(identifier, level):
    directory = os.path.join(current_app.static_folder, 'assets', 'plants', identifier)
    filename_base = f"level_{level}"

    # Check for video first, then fallback to images
    for ext in ['mp4', 'gif', 'png', 'webp']:
        if os.path.exists(os.path.join(directory, f"{filename_base}.{ext}")):
            return send_from_directory(directory, f"{filename_base}.{ext}")

    return send_from_directory(directory, "placeholder.png")



@bp.get("/greet")
@login_required
def greet():
    old_dog, new_dog = check_for_companion_succession(current_user)

    is_transition = False
    if old_dog:
        is_transition = True
        # Flip the flag now that we are officially showing the memorial
        old_dog.death_acknowledged = True
        db.session.commit()

    # User name logic we discussed earlier
    user_display_name = current_user.name.split()[0] if current_user.name else "Learner"

    # Expression logic based on balance
    balance = get_balance_ticks(current_user.id, asset_id="access_note")
    expression = "happy" if balance > 5000 else "pain"

    possible_messages = get_companion_message(
        new_dog.name,
        expression,
        is_transition,
        old_dog_name=old_dog.name if old_dog else None,
        user_name=current_user.name.split()[0] if current_user.name else "Learner"
    )

    return render_template(
        "companion/greet.html",
        dog=new_dog,
        old_dog=old_dog,
        is_transition=is_transition,
        expression=expression,
        user_display_name=user_display_name,
        companion_speech=random.choice(possible_messages)
    )



@bp.get("/garden")
@login_required
def view_garden():
    # 1. Get the current active companion
    dog = Character.query.filter_by(user_id=current_user.id, is_alive=True).first()

    # 2. Get the garden items for the active dog
    items = Garden.query.filter_by(character_id=dog.id).all() if dog else []

    # 3. Get the "Hall of Fame" (all dead dogs, most recent first)
    departed = Character.query.filter_by(user_id=current_user.id, is_alive=False) \
        .order_by(Character.died_at.desc()).all()

    return render_template("companion/garden.html",
                           dog=dog,
                           items=items,
                           departed=departed)



@bp.post("/gift")
@login_required
def gift_seed():  # <--- This name must match url_for('companion.gift_seed')
    # For now, we'll hardcode a 'cyber_fern' seed to test
    success, message = buy_seed_for_dog(current_user, "cyber_fern", 1)

    if success:
        flash(message, "success")
    else:
        flash(message, "error")

    return redirect(url_for("companion.view_garden"))


@bp.get("/welcome")
@login_required
def welcome_explainer():
    # If they've already seen the intro, send them to the regular greet
    if current_user.has_seen_intro:
        return redirect(url_for('companion.greet'))

    # Get the current stage from the URL (default to 1)
    stage = request.args.get('stage', 1, type=int)

    # Define Dexter's messages and images for each stage
    stages = {
        1: {
            "image": "mascot/wave.png",
            "text": f"Hi there! I'm Dexter. I'm so happy you're here at GamifyLearning!",
            "btn": "Hi Dexter!"
        },
        2: {
            "image": "mascot/stand.png",
            "text": "You're about to meet your very own encouraging companion who will grow alongside you.",
            "btn": "I'm ready to meet them"
        },
        3: {
            "image": "mascot/goodluck.png",
            "text": "Good luck with your studies! I'll be rooting for you.",
            "btn": "Let's go!"
        }
    }

    # Safety fallback if stage is out of bounds
    if stage not in stages:
        return redirect(url_for('companion.welcome_explainer', stage=1))

    return render_template("companion/welcome.html",
                           stage_data=stages[stage],
                           stage=stage)


@bp.post("/welcome/next/<int:current_stage>")
@login_required
def welcome_next(current_stage):
    if current_stage < 3:
        # Move to the next Dexter slide
        return redirect(url_for('companion.welcome_explainer', stage=current_stage + 1))
    else:
        # Final stage: Flip the flag and go to the companion greeting
        current_user.has_seen_intro = True
        db.session.commit()
        return redirect(url_for('companion.greet'))