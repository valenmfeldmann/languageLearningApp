# app/author/routes.py
from __future__ import annotations
from app.access_ledger.service import (
    get_curriculum_share_asset,
    get_or_create_user_wallet,
    post_access_txn,
    EntrySpec, get_or_create_system_account,
)

import json
import zipfile
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.extensions import db

from app.models import (
    Lesson, LessonBlock, LessonAsset,
    Curriculum, CurriculumOwner, AccessAccount,
    CurriculumItem, LessonSubject,  # optional, since you import inside fns today
)
from flask import abort, jsonify
from sqlalchemy import and_
import bleach
from bleach.css_sanitizer import CSSSanitizer
from app.models import LessonSubject, LessonSchool  # new model
import os
import time
from werkzeug.utils import secure_filename
from flask import current_app, url_for
from app.models import CurriculumEditor, User
import shutil
import os
from flask import current_app
from sqlalchemy import func  # <-- Add this line


DEFAULT_SHARES = 100


bp = Blueprint("author", __name__, url_prefix="/author")


def _assets_root() -> Path:
    # Put assets under instance/lesson_assets/<lesson_id>/...
    root = Path(current_app.instance_path) / "lesson_assets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _require_author() -> None:
    # Minimal gate for now. Replace with a real role later.
    # Example: allow only specific email domain, or a boolean field on User.
    allowed = True
    if not allowed:
        raise PermissionError("not_author")

def _require_lesson_edit_perm(lesson: Lesson) -> None:
    # v1 rule: only creator can edit
    if not lesson.created_by_user_id or lesson.created_by_user_id != current_user.id:
        raise PermissionError("not_lesson_owner")


# def _require_curriculum_perm(curriculum_id: str, *, need_edit: bool = False, need_manage: bool = False):
#     from app.models import CurriculumOwner, CurriculumEditor
#
#     # Owner row (shareholders)
#     row = (CurriculumOwner.query
#            .filter_by(curriculum_id=curriculum_id, user_id=current_user.id)
#            .one_or_none())
#
#     # Editor row (non-owners)
#     ed = (CurriculumEditor.query
#           .filter_by(curriculum_id=curriculum_id, user_id=current_user.id)
#           .one_or_none())
#
#     # "has any relationship"
#     if (not row or row.shares <= 0) and not ed:
#         raise PermissionError("not_owner_or_editor")
#
#     # Manage stays “top owner only” (your existing rule)
#     if need_manage:
#         if not row or row.shares <= 0:
#             raise PermissionError("not_admin")
#         top = (CurriculumOwner.query
#                .filter_by(curriculum_id=curriculum_id)
#                .order_by(CurriculumOwner.shares.desc(),
#                          CurriculumOwner.created_at.asc(),
#                          CurriculumOwner.id.asc())
#                .first())
#         is_owner = bool(top and top.user_id == current_user.id)
#         if not is_owner:
#             raise PermissionError("not_admin")
#
#     # Edit: owners with can_edit OR editors with can_edit
#     if need_edit:
#         owner_ok = bool(row and row.shares > 0 and row.can_edit)
#         editor_ok = bool(ed and ed.can_edit)
#         if not (owner_ok or editor_ok):
#             raise PermissionError("not_editor")
#
#     return row


def _require_curriculum_perm(curriculum_id: str, *, need_edit: bool = False, need_manage: bool = False):
    from app.models import CurriculumOwner, CurriculumEditor
    from flask import abort

    # 1. Fetch the relationship rows
    row = CurriculumOwner.query.filter_by(curriculum_id=curriculum_id, user_id=current_user.id).one_or_none()
    ed = CurriculumEditor.query.filter_by(curriculum_id=curriculum_id, user_id=current_user.id).one_or_none()

    # 2. Basic Access: Must have some relationship to the project
    if (not row or row.shares <= 0) and not ed:
        abort(403)

    # 3. Manage Permission: This is what you wanted to change.
    # We now allow ANY owner with shares OR ANY editor with can_edit to add others.
    if need_manage:
        owner_can_manage = bool(row and row.shares > 0 and row.can_edit)
        editor_can_manage = bool(ed and ed.can_edit)

        if not (owner_can_manage or editor_can_manage):
            # If you still want a "Super Admin" level for deleting the curriculum,
            # you would check for the Top Owner here. But for adding editors,
            # we just need to know they are an authorized editor.
            abort(403)

    # 4. Edit Permission: Content changes
    if need_edit:
        owner_ok = bool(row and row.shares > 0 and row.can_edit)
        editor_ok = bool(ed and ed.can_edit)
        if not (owner_ok or editor_ok):
            abort(403)

    return row

def _load_json_file(file_storage) -> dict[str, Any]:
    raw = file_storage.read()
    try:
        obj = json.loads(raw)
    except Exception as e:
        raise ValueError(f"lesson.json is not valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("lesson.json root must be an object")
    return obj


def _validate_lesson_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Schema updated to support Multimedia MCQs (Audio/Image).
    """
    code = payload.get("code")
    title = payload.get("title")
    blocks = payload.get("blocks")

    if not isinstance(code, str) or not code.strip():
        raise ValueError("Missing/invalid 'code' (string)")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Missing/invalid 'title' (string)")
    if not isinstance(blocks, list) or len(blocks) == 0:
        raise ValueError("Missing/invalid 'blocks' (non-empty list)")

    allowed_types = {
        "markdown",
        "video_url",
        "video_asset",
        "audio_asset",
        "quiz_mcq",
        "desmos",
        "html_safe",
        "callout",
        "reveal",
        "trivia_launcher",
        "timer",
    }

    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            raise ValueError(f"blocks[{i}] must be an object")
        t = b.get("type")
        p = b.get("payload")
        if t not in allowed_types:
            raise ValueError(f"blocks[{i}].type must be one of {sorted(allowed_types)}")
        if not isinstance(p, dict):
            raise ValueError(f"blocks[{i}].payload must be an object")

        if t == "markdown":
            if not isinstance(p.get("text"), str):
                raise ValueError(f"blocks[{i}] markdown requires payload.text (string)")

        elif t == "video_url":
            url = p.get("url")
            if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
                raise ValueError(f"blocks[{i}] video_url requires payload.url (http/https string)")

        elif t == "quiz_mcq":
            prompt = p.get("prompt")
            choices = p.get("choices")
            answer_index = p.get("answer_index")

            # FIX: Allow prompt to be string OR dict (for audio prompts)
            if not isinstance(prompt, (str, dict)):
                raise ValueError(f"blocks[{i}] quiz_mcq requires prompt (string or media object)")

            # FIX: Allow each choice to be string OR dict (for image responses)
            if not isinstance(choices, list) or len(choices) < 2:
                raise ValueError(f"blocks[{i}] quiz_mcq requires payload.choices (list of >=2 items)")

            for j, c in enumerate(choices):
                if not isinstance(c, (str, dict)):
                    raise ValueError(f"blocks[{i}].choices[{j}] must be string or media object")

            if not isinstance(answer_index, int) or not (0 <= answer_index < len(choices)):
                raise ValueError(f"blocks[{i}] quiz_mcq requires payload.answer_index (int in range)")

        elif t == "timer":
            if not isinstance(p.get("seconds"), int):
                raise ValueError(f"blocks[{i}] timer requires payload.seconds (int)")

        elif t in ("video_asset", "audio_asset"):
            ref = p.get("ref")
            if not isinstance(ref, str) or not ref.startswith("assets/"):
                raise ValueError(f"blocks[{i}] {t} requires payload.ref like 'assets/...'")

    # Asset section companion preserved
    assets = payload.get("assets", [])
    if assets is not None:
        if not isinstance(assets, list):
            raise ValueError("'assets' must be a list if present")
        for i, a in enumerate(assets):
            if not isinstance(a, dict):
                raise ValueError(f"assets[{i}] must be an object")
            ref = a.get("ref")
            if not isinstance(ref, str) or not ref.strip() or not ref.startswith("assets/"):
                raise ValueError(f"assets[{i}].ref must start with 'assets/'")

    return payload


# def _validate_lesson_payload(payload: dict[str, Any]) -> dict[str, Any]:
#     """
#     Minimal schema:
#     {
#       "code": "spanish-basics-01",
#       "title": "Basics 1",
#       "description": "...",            (optional)
#       "language_code": "es",           (optional)
#       "is_published": false,           (optional)
#       "blocks": [
#         {"type": "markdown", "payload": {"text": "..."}},
#         {"type": "video_url", "payload": {"url": "https://..."}},
#         {"type": "quiz_mcq", "payload": {...}}
#       ],
#       "assets": [
#         {"ref": "assets/img1.png", "content_type": "image/png"}   (optional)
#       ]
#     }
#     """
#     code = payload.get("code")
#     title = payload.get("title")
#     blocks = payload.get("blocks")
#
#     if not isinstance(code, str) or not code.strip():
#         raise ValueError("Missing/invalid 'code' (string)")
#     if not isinstance(title, str) or not title.strip():
#         raise ValueError("Missing/invalid 'title' (string)")
#     if not isinstance(blocks, list) or len(blocks) == 0:
#         raise ValueError("Missing/invalid 'blocks' (non-empty list)")
#
#
#     allowed_types = {
#         "markdown",
#         "video_url",
#         "video_asset",
#         "audio_asset",
#         "quiz_mcq",
#         "desmos",
#         "html_safe",
#         "callout",
#         "reveal",
#     }
#
#     for i, b in enumerate(blocks):
#         if not isinstance(b, dict):
#             raise ValueError(f"blocks[{i}] must be an object")
#         t = b.get("type")
#         p = b.get("payload")
#         if t not in allowed_types:
#             raise ValueError(f"blocks[{i}].type must be one of {sorted(allowed_types)}")
#         if not isinstance(p, dict):
#             raise ValueError(f"blocks[{i}].payload must be an object")
#
#         # Type-specific checks (minimal but catches common mistakes)
#         if t == "markdown":
#             if not isinstance(p.get("text"), str):
#                 raise ValueError(f"blocks[{i}] markdown requires payload.text (string)")
#         elif t == "video_url":
#             url = p.get("url")
#             if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
#                 raise ValueError(f"blocks[{i}] video_url requires payload.url (http/https string)")
#         elif t == "quiz_mcq":
#             prompt = p.get("prompt")
#             choices = p.get("choices")
#             answer_index = p.get("answer_index")
#             if not isinstance(prompt, str) or not prompt.strip():
#                 raise ValueError(f"blocks[{i}] quiz_mcq requires payload.prompt (string)")
#             if not isinstance(choices, list) or len(choices) < 2 or not all(isinstance(c, str) for c in choices):
#                 raise ValueError(f"blocks[{i}] quiz_mcq requires payload.choices (list of >=2 strings)")
#             if not isinstance(answer_index, int) or not (0 <= answer_index < len(choices)):
#                 raise ValueError(f"blocks[{i}] quiz_mcq requires payload.answer_index (int in range)")
#         elif t in ("video_asset", "audio_asset"):
#             ref = p.get("ref")
#             if not isinstance(ref, str) or not ref.startswith("assets/"):
#                 raise ValueError(f"blocks[{i}] {t} requires payload.ref like 'assets/...'")
#
#     assets = payload.get("assets", [])
#     if assets is not None:
#         if not isinstance(assets, list):
#             raise ValueError("'assets' must be a list if present")
#         for i, a in enumerate(assets):
#             if not isinstance(a, dict):
#                 raise ValueError(f"assets[{i}] must be an object")
#             ref = a.get("ref")
#             if not isinstance(ref, str) or not ref.strip():
#                 raise ValueError(f"assets[{i}].ref must be a string")
#             # Keep refs inside "assets/" namespace to avoid path weirdness
#             if not ref.startswith("assets/"):
#                 raise ValueError(f"assets[{i}].ref must start with 'assets/'")
#
#     return payload


# app/author/routes.py

def _append_lesson_to_curriculum(lesson_id, curriculum_id):
    """Adds a lesson to the end of a specific curriculum."""
    last_pos = (db.session.query(func.max(CurriculumItem.position))
                .filter_by(curriculum_id=curriculum_id)
                .scalar())
    next_pos = (last_pos + 1) if last_pos is not None else 0
    db.session.add(CurriculumItem(
        curriculum_id=curriculum_id,
        position=next_pos,
        item_type="lesson",
        lesson_id=lesson_id
    ))



def _save_zip_assets_to_lesson(z, info_list, lesson_id, folder_prefix):
    root = _assets_root() / lesson_id / "assets"
    root.mkdir(parents=True, exist_ok=True)

    for info in info_list:
        # Strip the folder prefix to get just the filename
        filename = info.filename.replace(folder_prefix + "assets/", "")
        if not filename or info.is_dir(): continue

        # Save the physical file
        target = root / secure_filename(filename)
        with z.open(info) as src, open(target, "wb") as dst:
            dst.write(src.read())

        # Register in DB
        # Ensure the 'ref' starts with 'assets/' for the lesson to find it
        db.session.add(LessonAsset(
            lesson_id=lesson_id,
            ref=f"assets/{filename}",
            storage_path=str(target),
            size_bytes=info.file_size
        ))


@bp.post("/curriculum/<curriculum_id>/batch_import")
@login_required
def curriculum_batch_import(curriculum_id):
    """Processes a single ZIP containing multiple lesson sub-folders."""
    _require_curriculum_perm(curriculum_id, need_edit=True)
    master_zip_fs = request.files.get("master_zip")

    if not master_zip_fs:
        return jsonify({"error": "No ZIP file provided"}), 400

    try:
        with zipfile.ZipFile(master_zip_fs) as z:
            # Find every 'lesson.json' in the ZIP
            json_paths = [i.filename for i in z.infolist() if i.filename.endswith('lesson.json')]

            for path in json_paths:
                folder_prefix = path.rsplit('lesson.json', 1)[0]

                with z.open(path) as f:
                    payload = json.loads(f.read())
                    payload = _validate_lesson_payload(payload)

                # 1. Create/Update Lesson
                lesson = Lesson.query.filter_by(code=payload["code"]).one_or_none()
                if not lesson:
                    lesson = Lesson(code=payload["code"], created_by_user_id=current_user.id)
                    db.session.add(lesson)

                lesson.title = payload["title"]
                db.session.flush()

                # 2. Cleanup and Save Assets
                LessonBlock.query.filter_by(lesson_id=lesson.id).delete()
                LessonAsset.query.filter_by(lesson_id=lesson.id).delete()

                lesson_assets = [i for i in z.infolist()
                                 if i.filename.startswith(folder_prefix + "assets/") and not i.is_dir()]
                _save_zip_assets_to_lesson(z, lesson_assets, lesson.id, folder_prefix)

                # 3. Create Blocks
                for idx, b in enumerate(payload.get("blocks", [])):
                    db.session.add(LessonBlock(lesson_id=lesson.id, position=idx,
                                               type=b["type"], payload_json=b["payload"]))

                # 4. Link to Curriculum
                _append_lesson_to_curriculum(lesson.id, curriculum_id)

        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500



def process_master_zip(zip_fs, curriculum_id, user_id):
    """
    Unpacks a ZIP, looks for every folder containing a lesson.json,
    and imports them all into the curriculum.
    """
    with zipfile.ZipFile(zip_fs) as z:
        # Find all lesson.json files regardless of how deep they are
        lesson_files = [info.filename for info in z.infolist() if info.filename.endswith('lesson.json')]

        for json_path in lesson_files:
            # Determine the folder prefix for this specific lesson
            folder_prefix = json_path.rsplit('lesson.json', 1)[0]

            # Extract the JSON and create a virtual "file stream" for it
            with z.open(json_path) as f:
                payload = json.loads(f.read())

            # 1. Process the Lesson metadata
            lesson = process_lesson_import_from_payload(payload, user_id)

            # 2. Extract only the assets belonging to this folder
            lesson_assets = [info for info in z.infolist()
                             if info.filename.startswith(folder_prefix + "assets/")
                             and not info.is_dir()]

            _save_zip_assets_to_lesson(z, lesson_assets, lesson.id, folder_prefix)

            # 3. Add to Curriculum
            _append_lesson_to_curriculum(lesson.id, curriculum_id)


def _extract_assets_zip(zip_fs, lesson_id: str) -> dict[str, tuple[str, int | None]]:
    """
    Extracts zip into instance/lesson_assets/<lesson_id>/...
    Returns map: ref -> (storage_path, size_bytes)
    We only accept files under assets/ in the zip.
    """
    root = _assets_root() / lesson_id
    root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_fs) as z:
        out: dict[str, tuple[str, int | None]] = {}
        for info in z.infolist():
            if info.is_dir():
                continue

            # Normalize names
            name = info.filename.replace("\\", "/")
            if not name.startswith("assets/"):
                continue  # ignore anything outside assets/

            # Prevent zip-slip
            target = (root / name).resolve()
            if not str(target).startswith(str(root.resolve())):
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())

            out[name] = (str(target), getattr(info, "file_size", None))
        return out


@bp.get("/import")
@login_required
def import_form():
    _require_author()
    return render_template("author/import.html")


@bp.post("/import")
@login_required
def import_post():
    _require_author()

    lesson_json_fs = request.files.get("lesson_json")
    assets_zip_fs = request.files.get("assets_zip")

    if not lesson_json_fs or lesson_json_fs.filename == "":
        flash("Please upload lesson.json", "error")
        return redirect(url_for("author.import_form"))

    # Load + validate lesson.json
    try:
        payload = _load_json_file(lesson_json_fs)
        payload = _validate_lesson_payload(payload)
    except Exception as e:
        flash(str(e), "error")
        return redirect(url_for("author.import_form"))


    code = payload["code"].strip()
    title = payload["title"].strip()
    desc = (payload.get("description") or None)
    lang = (payload.get("language_code") or None)
    is_published = bool(payload.get("is_published") or False)


    # Upsert lesson by code
    lesson = Lesson.query.filter_by(code=code).one_or_none()
    if lesson:
        lesson.title = title
        lesson.description = desc
        lesson.language_code = lang
        lesson.created_by_user_id = current_user.id
        lesson.is_published = is_published

        # replace blocks always
        LessonBlock.query.filter_by(lesson_id=lesson.id).delete()

        # ONLY replace assets if a zip was provided
        if assets_zip_fs and assets_zip_fs.filename:
            LessonAsset.query.filter_by(lesson_id=lesson.id).delete()

        db.session.flush()
    else:

        # Create Lesson
        lesson = Lesson(
            code=payload["code"].strip(),
            title=payload["title"].strip(),
            description=(payload.get("description") or None),
            language_code=(payload.get("language_code") or None),
            created_by_user_id=current_user.id,
            is_published=bool(payload.get("is_published") or False),
        )
        db.session.add(lesson)
        db.session.flush()  # get lesson.id

    # Extract assets (optional)
    extracted: dict[str, tuple[str, int | None]] = {}
    if assets_zip_fs and assets_zip_fs.filename:
        try:
            extracted = _extract_assets_zip(assets_zip_fs, lesson.id)
        except Exception as e:
            db.session.rollback()
            flash(f"assets.zip failed to extract: {e}", "error")
            return redirect(url_for("author.import_form"))

    # Insert LessonAsset rows based on manifest OR extracted content
    # Preferred: use payload["assets"] as manifest if present.
    manifest_assets = payload.get("assets") or []
    if manifest_assets:
        for a in manifest_assets:
            ref = a["ref"]

            if assets_zip_fs and assets_zip_fs.filename:
                # zip import: must exist in extracted
                if ref not in extracted:
                    db.session.rollback()
                    flash(f"Asset listed in lesson.json not found in zip: {ref}", "error")
                    return redirect(url_for("author.import_form"))

                storage_path, size_bytes = extracted[ref]
                db.session.add(LessonAsset(
                    lesson_id=lesson.id,
                    ref=ref,
                    storage_path=storage_path,
                    content_type=a.get("content_type"),
                    size_bytes=size_bytes,
                ))
            else:
                # no zip: keep existing asset row if present (do NOT create blank paths)
                existing = LessonAsset.query.filter_by(lesson_id=lesson.id, ref=ref).one_or_none()
                if existing:
                    existing.content_type = a.get("content_type") or existing.content_type
                else:
                    # optional: allow manifest to reference not-yet-uploaded assets, but don't serve them
                    # (or you can make this a hard error if you prefer)
                    pass
    else:
        # No manifest; if zip exists, register every extracted file
        for ref, (storage_path, size_bytes) in extracted.items():
            db.session.add(
                LessonAsset(
                    lesson_id=lesson.id,
                    ref=ref,
                    storage_path=storage_path,
                    content_type=None,
                    size_bytes=size_bytes,
                )
            )

    # Insert blocks in order
    for idx, b in enumerate(payload["blocks"]):
        db.session.add(
            LessonBlock(
                lesson_id=lesson.id,
                position=idx,
                type=b["type"],
                payload_json=b["payload"],
            )
        )

    db.session.commit()
    flash("Lesson imported.", "success")
    # return redirect(url_for("main.lesson_page", lesson_id=lesson.id))
    # return redirect(url_for("main.lesson_page", code=lesson.code))

    return redirect(url_for("main.lesson_page", lesson_code=lesson.code))


# app/author/routes.py

@bp.get("/curricula")
@login_required
def curricula_index():
    _require_author()
    # Fetch curricula where the user has an ownership stake
    curricula = (Curriculum.query
                 .join(CurriculumOwner)
                 .filter(CurriculumOwner.user_id == current_user.id)
                 .order_by(Curriculum.updated_at.desc())
                 .all())

    return render_template("author/curriculum_index.html", curricula=curricula)



# @bp.post("/lesson/<lesson_id>/apply_json")
# @login_required
# def lesson_apply_json(lesson_id: str):
#     _require_author()
#     lesson = Lesson.query.get_or_404(lesson_id)
#
#     try:
#         _require_lesson_edit_perm(lesson)
#     except PermissionError:
#         abort(403)
#
#     # Accept either: pasted JSON OR uploaded lesson.json file
#     raw_text = (request.form.get("lesson_json_text") or "").strip()
#     lesson_json_fs = request.files.get("lesson_json")
#
#     try:
#         if raw_text:
#             payload = json.loads(raw_text)
#         elif lesson_json_fs and lesson_json_fs.filename:
#             payload = _load_json_file(lesson_json_fs)
#         else:
#             flash("Provide lesson JSON (paste or upload lesson.json).", "error")
#             return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))
#
#         payload = _validate_lesson_payload(payload)
#     except Exception as e:
#         flash(f"Invalid lesson JSON: {e}", "error")
#         return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))
#
#     # Optional: assets.zip (same as import)
#     assets_zip_fs = request.files.get("assets_zip")
#
#     # Replace lesson metadata from payload (but keep lesson.id + ownership)
#     # keep existing code; allow everything else
#     # lesson.code = lesson.code
#     lesson.title = payload["title"].strip()
#     lesson.description = (payload.get("description") or None)
#     lesson.language_code = (payload.get("language_code") or None)
#     lesson.is_published = bool(payload.get("is_published") or False)
#
#     # Replace contents
#     LessonBlock.query.filter_by(lesson_id=lesson.id).delete()
#     LessonAsset.query.filter_by(lesson_id=lesson.id).delete()
#     db.session.flush()
#
#     extracted: dict[str, tuple[str, int | None]] = {}
#     if assets_zip_fs and assets_zip_fs.filename:
#         try:
#             extracted = _extract_assets_zip(assets_zip_fs, lesson.id)
#         except Exception as e:
#             db.session.rollback()
#             flash(f"assets.zip failed to extract: {e}", "error")
#             return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))
#
#     # Assets: prefer manifest if present
#     manifest_assets = payload.get("assets") or []
#     if manifest_assets:
#         for a in manifest_assets:
#             ref = a["ref"]
#             if assets_zip_fs and assets_zip_fs.filename and ref not in extracted:
#                 db.session.rollback()
#                 flash(f"Asset listed in lesson.json not found in zip: {ref}", "error")
#                 return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))
#
#             storage_path, size_bytes = extracted.get(ref, ("", None))
#             db.session.add(
#                 LessonAsset(
#                     lesson_id=lesson.id,
#                     ref=ref,
#                     storage_path=storage_path,
#                     content_type=a.get("content_type"),
#                     size_bytes=size_bytes,
#                 )
#             )
#     else:
#         for ref, (storage_path, size_bytes) in extracted.items():
#             db.session.add(
#                 LessonAsset(
#                     lesson_id=lesson.id,
#                     ref=ref,
#                     storage_path=storage_path,
#                     content_type=None,
#                     size_bytes=size_bytes,
#                 )
#             )
#
#     # Blocks
#     for idx, b in enumerate(payload["blocks"]):
#         db.session.add(
#             LessonBlock(
#                 lesson_id=lesson.id,
#                 position=idx,
#                 type=b["type"],
#                 payload_json=b["payload"],
#             )
#         )
#
#     db.session.commit()
#     flash("Applied lesson JSON to this lesson.", "success")
#     return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))


# app/author/routes.py

@bp.post("/lesson/<lesson_id>/apply_json")
@login_required
def lesson_apply_json(lesson_id: str):
    _require_author()
    lesson = Lesson.query.get_or_404(lesson_id)

    try:
        _require_lesson_edit_perm(lesson)
    except PermissionError:
        abort(403)

    # 1. Load and Validate JSON
    raw_text = (request.form.get("lesson_json_text") or "").strip()
    lesson_json_fs = request.files.get("lesson_json")

    try:
        if raw_text:
            payload = json.loads(raw_text)
        elif lesson_json_fs and lesson_json_fs.filename:
            payload = _load_json_file(lesson_json_fs)
        else:
            flash("Provide lesson JSON (paste or upload lesson.json).", "error")
            return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))

        payload = _validate_lesson_payload(payload)
    except Exception as e:
        flash(f"Invalid lesson JSON: {e}", "error")
        return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))

    # 2. Update Lesson Metadata
    lesson.title = payload["title"].strip()
    lesson.description = (payload.get("description") or None)
    lesson.language_code = (payload.get("language_code") or None)
    lesson.is_published = bool(payload.get("is_published") or False)

    # 3. Content Replacement: Blocks are always replaced
    LessonBlock.query.filter_by(lesson_id=lesson.id).delete()

    # --- CRITICAL CHANGE: Asset erasure removed ---
    # LessonAsset.query.filter_by(lesson_id=lesson.id).delete() <--- Wiped this line
    db.session.flush()

    # 4. Handle ZIP Extraction (if provided)
    assets_zip_fs = request.files.get("assets_zip")
    extracted: dict[str, tuple[str, int | None]] = {}
    if assets_zip_fs and assets_zip_fs.filename:
        try:
            extracted = _extract_assets_zip(assets_zip_fs, lesson.id)
        except Exception as e:
            db.session.rollback()
            flash(f"assets.zip failed to extract: {e}", "error")
            return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))

    # 5. Intelligent Asset Merging (Upsert Pattern)
    manifest_assets = payload.get("assets") or []
    if manifest_assets:
        for a in manifest_assets:
            ref = a["ref"]

            # Check if this asset already exists in the database
            existing = LessonAsset.query.filter_by(lesson_id=lesson.id, ref=ref).one_or_none()
            storage_path, size_bytes = extracted.get(ref, (None, None))

            if existing:
                # Update existing record ONLY if we have new file data from a zip
                if storage_path:
                    existing.storage_path = storage_path
                    existing.size_bytes = size_bytes
                existing.content_type = a.get("content_type") or existing.content_type
            elif storage_path:
                # Create NEW record only if a file was actually provided in the zip
                db.session.add(LessonAsset(
                    lesson_id=lesson.id,
                    ref=ref,
                    storage_path=storage_path,
                    content_type=a.get("content_type"),
                    size_bytes=size_bytes,
                ))
    else:
        # Fallback: if no manifest, update/add based solely on ZIP content
        for ref, (storage_path, size_bytes) in extracted.items():
            existing = LessonAsset.query.filter_by(lesson_id=lesson.id, ref=ref).one_or_none()
            if existing:
                existing.storage_path = storage_path
                existing.size_bytes = size_bytes
            else:
                db.session.add(LessonAsset(
                    lesson_id=lesson.id,
                    ref=ref,
                    storage_path=storage_path,
                    size_bytes=size_bytes
                ))

    # 6. Re-insert Blocks
    for idx, b in enumerate(payload["blocks"]):
        db.session.add(
            LessonBlock(
                lesson_id=lesson.id,
                position=idx,
                type=b["type"],
                payload_json=b["payload"],
            )
        )

    db.session.commit()
    flash("Applied lesson JSON to this lesson. Existing assets were preserved.", "success")
    return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))




@bp.post("/lesson/<lesson_id>/delete")
@login_required
def lesson_delete(lesson_id):
    # 1. Fetch the lesson and ensure the current user owns it
    lesson = Lesson.query.filter_by(id=lesson_id, created_by_user_id=current_user.id).first_or_404()

    # 2. Path to the assets folder (e.g., instance/lesson_assets/<lesson_id>)
    # Adjust this based on your actual storage path logic
    assets_dir = os.path.join(current_app.instance_path, 'lesson_assets', lesson.id)

    try:
        # 3. Delete physical files first
        if os.path.exists(assets_dir):
            shutil.rmtree(assets_dir)
            print(f"Cleaned up assets for lesson {lesson.id}")

        # 4. Delete the database record
        # CASCADE in your models.py will automatically handle LessonBlock and LessonAsset records
        db.session.delete(lesson)
        db.session.commit()

        flash("Lesson and assets permanently deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error during deletion: {str(e)}", "error")
        print(f"Deletion error: {e}")

    return redirect(request.referrer or url_for('author.lesson_index'))



@bp.get("/lessons")
@login_required
def lesson_index():
    _require_author()
    lessons = (Lesson.query
               .filter_by(created_by_user_id=current_user.id)
               .order_by(Lesson.updated_at.desc())
               .all())
    return render_template("author/lesson_index.html", lessons=lessons)


# @bp.get("/lesson/new")
# @login_required
# def lesson_new_form():
#     _require_author()
#     return render_template("author/lesson_new.html")

@bp.get("/lesson/new")
@login_required
def lesson_new_form():
    _require_author()

    subjects = (LessonSubject.query
                .filter(LessonSubject.active.is_(True))
                .order_by(LessonSubject.name.asc())
                .all())

    return render_template("author/lesson_new.html", subjects=subjects)


@bp.post("/lesson/new")
@login_required
def lesson_new_post():
    _require_author()

    subject_code = (request.form.get("subject_code") or "").strip() or None
    code = (request.form.get("code") or "").strip()
    title = (request.form.get("title") or "").strip()
    desc = (request.form.get("description") or "").strip() or None
    lang = (request.form.get("language_code") or "").strip() or None

    if subject_code:
        subj = (LessonSubject.query
                .filter_by(code=subject_code, active=True)
                .one_or_none())
        if not subj:
            flash("Invalid subject.", "error")
            return redirect(url_for("author.lesson_new_form"))

    if not code or not title:
        flash("Missing code/title", "error")
        return redirect(url_for("author.lesson_new_form"))

    existing = Lesson.query.filter_by(code=code).one_or_none()
    if existing:
        flash("Lesson code already exists.", "error")
        return redirect(url_for("author.lesson_new_form"))

    lesson = Lesson(
        code=code,
        title=title,
        description=desc,
        language_code=lang,
        subject_code=subject_code,
        created_by_user_id=current_user.id,
        visibility="private",
        is_published=False,
    )
    db.session.add(lesson)
    db.session.flush()

    # starter block
    db.session.add(LessonBlock(
        lesson_id=lesson.id,
        position=0,
        type="markdown",
        payload_json={"text": "New lesson. Edit me."},
    ))
    db.session.commit()

    return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))






ALLOWED_TAGS = [
    "p","br","hr","div","span",
    "b","strong","i","em","u","s",
    "h1","h2","h3","h4","h5","h6",
    "ul","ol","li",
    "blockquote","code","pre",
    "a","img", "style",
    "table","thead","tbody","tr","th","td",
]

ALLOWED_ATTRS = {
    "*": ["class", "title", "style"],
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height", "style"],
}

ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

css_sanitizer = CSSSanitizer(
    allowed_css_properties=[
        # keep this tight; add as you discover needs
        "color", "background-color",
        "font-size", "font-weight", "font-style", "text-decoration",
        "text-align", "line-height",
        "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
        "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
        "border", "border-width", "border-style", "border-color", "border-radius",
        "width", "height", "max-width",
        "display",
    ],
    # optionally: allow_css_variables=False (default) keeps it stricter
)

def sanitize_html(user_html: str) -> str:
    cleaned = bleach.clean(
        user_html or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        css_sanitizer=css_sanitizer,
    )
    return bleach.linkify(cleaned)



@bp.post("/lesson/<lesson_id>/blocks/<block_id>/update_form")
@login_required
def lesson_block_update_form(lesson_id: str, block_id: str):
    _require_author()
    lesson = Lesson.query.get_or_404(lesson_id)
    _require_lesson_edit_perm(lesson)

    b = LessonBlock.query.filter_by(id=block_id, lesson_id=lesson.id).one_or_none()
    if not b:
        abort(404)

    if b.type == "markdown":
        b.payload_json = {"text": request.form.get("text") or ""}

    elif b.type == "video_url":
        b.payload_json = {
            "url": request.form.get("url") or "",
            "caption": request.form.get("caption") or "",
        }



    elif b.type == "quiz_mcq":
        payload = dict(b.payload_json or {})

        # 1. Mode Logic (Preserved)
        mode = (request.form.get("mode") or "graded").strip()
        if mode not in ("graded", "ungraded"):
            mode = "graded"
        payload["mode"] = mode

        # 2. Typed Prompt Logic (Preserved)
        prompt_kind = (request.form.get("prompt_kind") or "text").strip()
        prompt_value = (request.form.get("prompt_value") or "").strip()
        if prompt_kind == "text":
            payload["prompt"] = prompt_value
        else:
            payload["prompt"] = {"kind": prompt_kind, "value": prompt_value}



        # 3. Choice Parsing (ENHANCED for Combined Text + Image)
        idxs = set()
        for k in request.form.keys():
            if k.startswith("choice_") and k.endswith("_value"):
                try:
                    idxs.add(int(k.split("_")[1]))
                except:
                    pass
        idxs = sorted(idxs)

        choices = []
        for i in idxs:
            kind = (request.form.get(f"choice_{i}_kind") or "text").strip()
            value = (request.form.get(f"choice_{i}_value") or "").strip()

            # --- THE CRITICAL FIX ---
            # The backend MUST explicitly look for the new '_text' key
            text_label = (request.form.get(f"choice_{i}_text") or "").strip()

            if kind == "combined" or (value and text_label):
                choices.append({"text": text_label, "image": value})
            elif kind == "text":
                choices.append(value)
            else:
                choices.append({"kind": kind, "value": value})

        payload["choices"] = choices



        # 4. Multiple Correct / Answer Index Logic (Preserved)
        raw_correct = request.form.getlist("correct_indices")
        if raw_correct:
            correct = []
            for x in raw_correct:
                try:
                    j = int(x)
                    if 0 <= j < len(choices):
                        correct.append(j)
                except ValueError:
                    pass
            correct = sorted(set(correct))
            payload["correct_indices"] = correct
            payload["answer_index"] = correct[0] if correct else 0
        else:
            try:
                ans = int(request.form.get("answer_index") or "0")
            except ValueError:
                ans = 0
            ans = max(0, min(ans, len(choices) - 1))
            payload["answer_index"] = ans
            payload.pop("correct_indices", None)

        b.payload_json = payload
        db.session.add(b)
        db.session.commit()




    elif b.type == "video_asset":
        b.payload_json = {
            "ref": (request.form.get("ref") or "").strip(),
            "caption": (request.form.get("caption") or "").strip(),
            "controls": bool(request.form.get("controls")),
            "autoplay": bool(request.form.get("autoplay")),
        }

    elif b.type == "audio_asset":
        b.payload_json = {
            "ref": (request.form.get("ref") or "").strip(),
            "caption": (request.form.get("caption") or "").strip(),
            "controls": bool(request.form.get("controls")),
            "autoplay": bool(request.form.get("autoplay")),
            "loop": bool(request.form.get("loop")),
        }

    elif b.type == "desmos":
        h = request.form.get("height") or "480"
        try:
            h = max(200, min(1200, int(h)))
        except Exception:
            h = 480
        b.payload_json = {
            "graph_url": (request.form.get("graph_url") or "").strip(),
            "height": h,
        }
    elif b.type == "html_safe":
        raw = request.form.get("html", "")
        b.payload_json = {"html": sanitize_html(raw)}

    elif b.type == "callout":
        variant = (request.form.get("variant") or "note").strip()
        title = (request.form.get("title") or "").strip()
        text = (request.form.get("text") or "").strip()

        b.payload_json = {
            "variant": variant,
            "title": title,
            "text": text,
        }

    elif b.type == "reveal":
        summary = (request.form.get("summary") or "Show").strip()
        text = (request.form.get("text") or "").strip()
        open_default = (request.form.get("open") == "on")

        b.payload_json = {
            "summary": summary,
            "text": text,
            "open": open_default,
        }


    elif b.type == "trivia_launcher":
        b.payload_json = {
            "kind": "trivia_launcher",
            "title": (request.form.get("title") or "Knowledge Check").strip(),
            "description": (request.form.get("description") or "").strip(),
            "query": (request.form.get("query") or "").strip(),
            "subject": (request.form.get("subject") or "").strip(),
        }

    elif b.type == "timer":
        try:
            sec = int(request.form.get("seconds") or "60")
        except ValueError:
            sec = 60
        b.payload_json = {"seconds": sec}

    else:
        # unknown type: do nothing
        pass

    db.session.commit()
    flash("Block saved.", "success")
    return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=b.id))




@bp.post("/lesson/<lesson_id>/blocks/<block_id>/mcq/add_choice")
@login_required
def mcq_add_choice(lesson_id: str, block_id: str):
    _require_author()
    lesson = Lesson.query.get_or_404(lesson_id)
    _require_lesson_edit_perm(lesson)

    b = LessonBlock.query.filter_by(id=block_id, lesson_id=lesson.id).one_or_none()
    if not b or b.type != "quiz_mcq":
        abort(404)

    payload = dict(b.payload_json or {})

    # MERGE current editor inputs (typed fields)
    payload["mode"] = (request.form.get("mode") or payload.get("mode") or "graded")

    prompt_kind = (request.form.get("prompt_kind") or "text").strip()
    prompt_value = (request.form.get("prompt_value") or "").strip()
    if prompt_kind == "text":
        payload["prompt"] = prompt_value
    else:
        payload["prompt"] = {"kind": prompt_kind, "value": prompt_value}

    # collect typed choices from submitted form
    idxs = set()
    for k in request.form.keys():
        if k.startswith("choice_") and k.endswith("_value"):
            try:
                idxs.add(int(k.split("_")[1]))
            except Exception:
                pass
    idxs = sorted(idxs)

    posted_choices = []
    for i in idxs:
        kind = (request.form.get(f"choice_{i}_kind") or "text").strip()
        value = (request.form.get(f"choice_{i}_value") or "").strip()
        if kind == "text":
            posted_choices.append(value)
        else:
            posted_choices.append({"kind": kind, "value": value})

    if posted_choices:
        payload["choices"] = posted_choices

    # Now add one blank choice
    choices = list(payload.get("choices") or ["", ""])
    if len(choices) < 2:
        choices += [""] * (2 - len(choices))
    choices.append("")
    payload["choices"] = choices

    # Keep indices sane
    try:
        ans = int(request.form.get("answer_index") or payload.get("answer_index") or 0)
    except ValueError:
        ans = 0
    payload["answer_index"] = max(0, min(ans, len(choices) - 1))

    b.payload_json = payload
    db.session.commit()
    return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=b.id))





@bp.post("/lesson/<lesson_id>/blocks/<block_id>/mcq/remove_choice_at")
@login_required
def mcq_remove_choice_at(lesson_id: str, block_id: str):
    _require_author()
    lesson = Lesson.query.get_or_404(lesson_id)
    _require_lesson_edit_perm(lesson)

    b = LessonBlock.query.filter_by(id=block_id, lesson_id=lesson.id).one_or_none()
    if not b or b.type != "quiz_mcq":
        abort(404)

    payload = dict(b.payload_json or {})

    # MERGE current editor inputs first
    payload["mode"] = (request.form.get("mode") or payload.get("mode") or "graded")
    payload["prompt"] = request.form.get("prompt") or payload.get("prompt") or ""

    # MERGE current editor inputs (typed fields)
    payload["mode"] = (request.form.get("mode") or payload.get("mode") or "graded")

    prompt_kind = (request.form.get("prompt_kind") or "text").strip()
    prompt_value = (request.form.get("prompt_value") or "").strip()
    if prompt_kind == "text":
        payload["prompt"] = prompt_value
    else:
        payload["prompt"] = {"kind": prompt_kind, "value": prompt_value}

    # collect typed choices from submitted form: choice_{i}_kind and choice_{i}_value
    idxs = set()
    for k in request.form.keys():
        if k.startswith("choice_") and k.endswith("_value"):
            try:
                idxs.add(int(k.split("_")[1]))
            except Exception:
                pass
    idxs = sorted(idxs)

    posted_choices = []
    for i in idxs:
        kind = (request.form.get(f"choice_{i}_kind") or "text").strip()
        value = (request.form.get(f"choice_{i}_value") or "").strip()
        if kind == "text":
            posted_choices.append(value)
        else:
            posted_choices.append({"kind": kind, "value": value})

    if posted_choices:
        payload["choices"] = posted_choices

    choices = list(payload.get("choices") or ["", ""])
    if len(choices) <= 2:
        return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=b.id))

    # idx comes from button name="idx" value="..."
    try:
        idx = int(request.form.get("idx") or "-1")
    except ValueError:
        idx = -1
    if idx < 0 or idx >= len(choices):
        return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=b.id))

    choices.pop(idx)

    # adjust answer_index
    try:
        ans = int(request.form.get("answer_index") or payload.get("answer_index") or 0)
    except ValueError:
        ans = 0
    if ans == idx:
        ans = 0
    elif ans > idx:
        ans -= 1
    ans = max(0, min(ans, len(choices) - 1))

    payload["choices"] = choices
    payload["answer_index"] = ans

    # adjust correct_indices if present
    corr = payload.get("correct_indices")
    if isinstance(corr, list):
        new_corr = []
        for c in corr:
            try:
                c = int(c)
            except ValueError:
                continue
            if c == idx:
                continue
            if c > idx:
                c -= 1
            if 0 <= c < len(choices):
                new_corr.append(c)
        payload["correct_indices"] = sorted(set(new_corr))

    b.payload_json = payload
    db.session.commit()
    return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=b.id))






@bp.get("/lesson/<lesson_id>/edit")
@login_required
def lesson_edit(lesson_id: str):
    _require_author()
    lesson = Lesson.query.get_or_404(lesson_id)

    try:
        _require_lesson_edit_perm(lesson)
    except PermissionError:
        abort(403)

    blocks = (LessonBlock.query
              .filter_by(lesson_id=lesson.id)
              .order_by(LessonBlock.position.asc())
              .all())
    assets = (LessonAsset.query
              .filter_by(lesson_id=lesson.id)
              .order_by(LessonAsset.created_at.desc())
              .all())

    selected_block_id = request.args.get("block_id") or (blocks[0].id if blocks else None)
    selected_block = next((b for b in blocks if b.id == selected_block_id), None)

    return render_template(
        "author/lesson_edit.html",
        lesson=lesson,
        blocks=blocks,
        assets=assets,
        selected_block=selected_block,
    )


@bp.post("/lesson/<lesson_id>/edit_meta")
@login_required
def lesson_edit_meta(lesson_id: str):
    _require_author()
    lesson = Lesson.query.get_or_404(lesson_id)

    try:
        _require_lesson_edit_perm(lesson)
    except PermissionError:
        abort(403)

    lesson.title = (request.form.get("title") or "").strip() or lesson.title
    lesson.description = (request.form.get("description") or "").strip() or None
    lesson.language_code = (request.form.get("language_code") or "").strip() or None
    lesson.visibility = (request.form.get("visibility") or "private").strip()
    lesson.is_published = bool(request.form.get("is_published"))

    db.session.commit()
    flash("Lesson saved.", "success")
    return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))


@bp.post("/lesson/<lesson_id>/blocks/add")
@login_required
def lesson_block_add(lesson_id: str):
    _require_author()
    lesson = Lesson.query.get_or_404(lesson_id)
    try:
        _require_lesson_edit_perm(lesson)
    except PermissionError:
        abort(403)

    block_type = (request.form.get("type") or "markdown").strip()

    # minimal defaults
    if block_type == "markdown":
        payload = {"text": ""}
    elif block_type == "video_url":
        payload = {"url": "", "caption": ""}
    elif block_type == "video_asset":
        payload = {"ref": "", "caption": "", "controls": True, "autoplay": False}
        # in lesson_block_add()
    elif block_type == "audio_asset":
        payload = {"ref": "", "caption": "", "controls": True, "autoplay": False, "loop": False}
    elif block_type == "desmos":
        payload = {"graph_url": "", "height": 480}
    elif block_type == "quiz_mcq":
        payload = {"prompt": "", "choices": ["", ""], "answer_index": 0}
    elif block_type == "callout":
        payload = {
            "variant": "note",  # note | info | tip | warn | danger
            "title": "",
            "text": "",
        }
    elif block_type == "reveal":
        payload = {
            "summary": "Show answer",
            "text": "",
            "open": False,  # default collapsed
        }
    elif block_type == "trivia_launcher":
        payload = {
            "kind": "trivia_launcher",  # Matches your macro check
            "title": "Knowledge Check",
            "description": "Ready to test what you learned?",
            "query": "",
            "subject": ""
        }
    elif block_type == "timer":
        payload = {"seconds": 60}
    else:
        payload = {}

    last_pos = (db.session.query(db.func.max(LessonBlock.position))
                .filter(LessonBlock.lesson_id == lesson.id)
                .scalar())
    next_pos = int(last_pos + 1) if last_pos is not None else 0

    b = LessonBlock(lesson_id=lesson.id, position=next_pos, type=block_type, payload_json=payload)
    db.session.add(b)
    db.session.commit()

    return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=b.id))


@bp.post("/lesson/<lesson_id>/blocks/<block_id>/delete")
@login_required
def lesson_block_delete(lesson_id: str, block_id: str):
    _require_author()
    lesson = Lesson.query.get_or_404(lesson_id)
    try:
        _require_lesson_edit_perm(lesson)
    except PermissionError:
        abort(403)

    b = LessonBlock.query.filter_by(id=block_id, lesson_id=lesson.id).one_or_none()
    if not b:
        abort(404)

    db.session.delete(b)
    db.session.flush()

    # re-pack positions 0..n-1 (two-phase to avoid uq collisions)
    blocks = (LessonBlock.query
              .filter_by(lesson_id=lesson.id)
              .order_by(LessonBlock.position.asc(), LessonBlock.id.asc())
              .all())

    # Phase 1: move everything out of the way
    OFFSET = 1000
    for i, bb in enumerate(blocks):
        bb.position = OFFSET + i
    db.session.flush()

    # Phase 2: assign final contiguous positions
    for i, bb in enumerate(blocks):
        bb.position = i

    db.session.commit()
    flash("Block deleted.", "success")
    return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))




@bp.post("/lesson/<lesson_id>/blocks/<block_id>/move")
@login_required
def lesson_block_move(lesson_id: str, block_id: str):
    _require_author()
    lesson = Lesson.query.get_or_404(lesson_id)
    _require_lesson_edit_perm(lesson)

    direction = (request.form.get("direction") or "").strip()

    blocks = (LessonBlock.query
              .filter_by(lesson_id=lesson.id)
              .order_by(LessonBlock.position.asc())
              .all())

    idx = next((i for i, bb in enumerate(blocks) if bb.id == block_id), None)
    if idx is None:
        abort(404)

    if direction == "up":
        if idx == 0:
            return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=block_id))
        a = blocks[idx]       # moving block
        b = blocks[idx - 1]   # block above
    elif direction == "down":
        if idx >= len(blocks) - 1:
            return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=block_id))
        a = blocks[idx]
        b = blocks[idx + 1]
    else:
        return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=block_id))

    # Swap using a temporary position to satisfy unique constraint (lesson_id, position)
    pos_a = int(a.position)
    pos_b = int(b.position)

    tmp = -1
    # (optional) ensure tmp isn't used; you can also use min(pos)-1
    a.position = tmp
    db.session.flush()   # apply tmp immediately

    b.position = pos_a
    db.session.flush()

    a.position = pos_b
    db.session.commit()

    return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=block_id))



@bp.post("/lesson/<lesson_id>/blocks/<block_id>/update")
@login_required
def lesson_block_update(lesson_id: str, block_id: str):
    _require_author()
    lesson = Lesson.query.get_or_404(lesson_id)
    try:
        _require_lesson_edit_perm(lesson)
    except PermissionError:
        abort(403)

    b = LessonBlock.query.filter_by(id=block_id, lesson_id=lesson.id).one_or_none()
    if not b:
        abort(404)

    # allow changing type (optional)
    new_type = (request.form.get("type") or b.type).strip()
    b.type = new_type

    # editor sends raw JSON payload
    raw = (request.form.get("payload_json") or "").strip()
    try:
        payload = json.loads(raw) if raw else {}
        if not isinstance(payload, dict):
            raise ValueError("payload_json must be a JSON object")
    except Exception as e:
        flash(f"Invalid JSON: {e}", "error")
        return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=b.id))

    b.payload_json = payload
    db.session.commit()

    flash("Block saved.", "success")
    return redirect(url_for("author.lesson_edit", lesson_id=lesson.id, block_id=b.id))


# @bp.post("/lesson/<lesson_id>/assets/upload")
# @login_required
# def lesson_asset_upload(lesson_id: str):
#     _require_author()
#     lesson = Lesson.query.get_or_404(lesson_id)
#     try:
#         _require_lesson_edit_perm(lesson)
#     except PermissionError:
#         abort(403)
#
#     fs = request.files.get("file")
#     if not fs or not fs.filename:
#         flash("No file selected.", "error")
#         return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))
#
#     # store under instance/lesson_assets/<lesson_id>/assets/<filename>
#     root = _assets_root() / lesson.id / "assets"
#     root.mkdir(parents=True, exist_ok=True)
#
#     filename = secure_filename(fs.filename)
#     if not filename:
#         flash("Bad filename.", "error")
#         return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))
#
#     target = (root / filename).resolve()
#     if not str(target).startswith(str(((_assets_root() / lesson.id).resolve()))):
#         flash("Refused path.", "error")
#         return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))
#
#     fs.save(target)
#
#     ref = f"assets/{filename}"
#     size_bytes = target.stat().st_size
#
#     existing = LessonAsset.query.filter_by(lesson_id=lesson.id, ref=ref).one_or_none()
#     if existing:
#         existing.storage_path = str(target)
#         existing.content_type = fs.mimetype
#         existing.size_bytes = size_bytes
#     else:
#         db.session.add(LessonAsset(
#             lesson_id=lesson.id,
#             ref=ref,
#             storage_path=str(target),
#             content_type=fs.mimetype,
#             size_bytes=size_bytes,
#         ))
#
#     db.session.commit()
#     flash(f"Uploaded {ref}", "success")
#     return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))
#


@bp.post("/lesson/<lesson_id>/assets/upload")
@login_required
def lesson_asset_upload(lesson_id: str):
    _require_author()
    lesson = Lesson.query.get_or_404(lesson_id)
    try:
        _require_lesson_edit_perm(lesson)
    except PermissionError:
        abort(403)

    # 1. Use getlist to capture multiple files from the 'files' input
    uploaded_files = request.files.getlist("files")

    if not uploaded_files or all(fs.filename == '' for fs in uploaded_files):
        flash("No files selected.", "error")
        return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))

    # Prepare storage root once
    root = _assets_root() / lesson.id / "assets"
    root.mkdir(parents=True, exist_ok=True)

    success_count = 0

    for fs in uploaded_files:
        if not fs or not fs.filename:
            continue

        filename = secure_filename(fs.filename)
        if not filename:
            continue

        # 2. Path security check (Preserved)
        target = (root / filename).resolve()
        parent_limit = (_assets_root() / lesson.id).resolve()
        if not str(target).startswith(str(parent_limit)):
            continue  # Skip malicious or invalid paths

        # 3. Save physical file
        fs.save(target)

        ref = f"assets/{filename}"
        size_bytes = target.stat().st_size

        # 4. Upsert companion (Preserved)
        existing = LessonAsset.query.filter_by(lesson_id=lesson.id, ref=ref).one_or_none()
        if existing:
            existing.storage_path = str(target)
            existing.content_type = fs.mimetype
            existing.size_bytes = size_bytes
        else:
            db.session.add(LessonAsset(
                lesson_id=lesson.id,
                ref=ref,
                storage_path=str(target),
                content_type=fs.mimetype,
                size_bytes=size_bytes,
            ))

        success_count += 1

    # 5. Finalize transaction
    db.session.commit()

    if success_count > 0:
        flash(f"Successfully uploaded {success_count} asset(s).", "success")
    else:
        flash("Failed to upload any valid assets.", "error")

    return redirect(url_for("author.lesson_edit", lesson_id=lesson.id))


# @bp.get("/curriculum/new")
# @login_required
# def curriculum_new_form():
#     _require_author()
#     return render_template("author/curriculum_new.html")



# @bp.get("/curriculum/new")
# @login_required
# def curriculum_new_form():
#     _require_author()
#
#     subjects = (LessonSubject.query
#         .filter(LessonSubject.active.is_(True))
#         .order_by(LessonSubject.name.asc())
#         .all())
#
#     return render_template(
#         "author/curriculum_new.html",
#         subjects=subjects,
#     )



@bp.get("/curriculum/new")
@login_required
def curriculum_new_form():
    _require_author()

    subjects = (LessonSubject.query
        .filter(LessonSubject.active.is_(True))
        .order_by(LessonSubject.name.asc())
        .all())

    schools = (LessonSchool.query
        .filter(LessonSchool.active.is_(True))
        .order_by(LessonSchool.name.asc())
        .all())

    return render_template("author/curriculum_new.html", subjects=subjects, schools=schools)



@bp.post("/curriculum/new")
@login_required
def curriculum_new_post():
    _require_author()
    from app.models import Curriculum, LessonSubject, LessonSchool

    code = (request.form.get("code") or "").strip()
    title = (request.form.get("title") or "").strip()
    desc = (request.form.get("description") or "").strip() or None
    subject_code = (request.form.get("subject_code") or "").strip() or None

    subject_code = (request.form.get("subject_code") or "").strip()
    school_code = (request.form.get("school_code") or "").strip()

    if not subject_code or not school_code:
        flash("Please select both a subject and a school.", "error")
        return redirect(url_for("author.curriculum_new_form"))

    subj = (LessonSubject.query
            .filter_by(code=subject_code, active=True)
            .one_or_none())
    if not subj:
        flash("Invalid subject.", "error")
        return redirect(url_for("author.curriculum_new_form"))

    sch = (LessonSchool.query
           .filter_by(code=school_code, active=True)
           .one_or_none())
    if not sch:
        flash("Invalid school.", "error")
        return redirect(url_for("author.curriculum_new_form"))

    if not code or not title:
        flash("Missing code/title", "error")
        return redirect(url_for("author.curriculum_new_form"))

    cur = Curriculum(
        code=code,
        title=title,
        description=desc,
        subject_code=subject_code,
        school_code=school_code,
        created_by_user_id=current_user.id,
    )
    db.session.add(cur)
    db.session.flush()  # get cur.id

    # Create curriculum wallet account
    wallet = AccessAccount(
        owner_user_id=None,
        account_type="curriculum_wallet",
        currency_code="access_note",
    )
    db.session.add(wallet)
    db.session.flush()

    cur.wallet_account_id = wallet.id

    # Creator owns 100% initially
    db.session.add(CurriculumOwner(
        curriculum_id=cur.id,
        user_id=current_user.id,
        shares=DEFAULT_SHARES,
        can_view_analytics=True,
        can_edit=True,
        can_manage_ownership=True,
    ))

    share_asset = get_curriculum_share_asset(cur.id)
    user_wallet = get_or_create_user_wallet(current_user.id)
    treasury = get_or_create_system_account("treasury")  # or "share_issuer"

    post_access_txn(
        event_type="curriculum_initial_mint",
        idempotency_key=f"curriculum_mint:{cur.id}",
        actor_user_id=current_user.id,
        context_type="curriculum",
        context_id=cur.id,
        entries=[
            EntrySpec(account_id=treasury.id, asset_id=share_asset.id, delta=-DEFAULT_SHARES, entry_type="mint"),
            EntrySpec(account_id=user_wallet.id, asset_id=share_asset.id, delta=+DEFAULT_SHARES, entry_type="mint"),
        ],
        forbid_user_overdraft=False,  # treasury can go negative if you treat it as issuer
    )

    db.session.commit()
    return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))


# @bp.get("/curriculum/<curriculum_id>/edit")
# @login_required
# def curriculum_edit(curriculum_id):
#     # Use your improved permission helper
#     _require_curriculum_perm(curriculum_id, need_edit=True)
#
#     cur = Curriculum.query.get_or_404(curriculum_id)
#
#     # NEW: You must fetch the lessons so the dropdown has data!
#     # Adjust this query if you want to filter which lessons appear
#     available_lessons = Lesson.query.order_by(Lesson.title.asc()).all()
#
#     return render_template(
#         "author/curriculum_edit.html",
#         curriculum=cur,
#         available_lessons=available_lessons  # <-- Pass it here
#     )



@bp.get("/curriculum/<curriculum_id>/edit")
@login_required
def curriculum_edit(curriculum_id: str):
    _require_author()
    from app.models import Curriculum, CurriculumItem, Lesson

    cur = Curriculum.query.get_or_404(curriculum_id)
    try:
        _require_curriculum_perm(cur.id, need_edit=True)
    except PermissionError:
        from flask import abort
        abort(403)

    items = (CurriculumItem.query
             .filter_by(curriculum_id=cur.id)
             .order_by(CurriculumItem.position.asc())
             .all())
    lessons = Lesson.query.order_by(Lesson.title.asc()).all()


    editors = (
        db.session.query(CurriculumEditor, User)
        .join(User, User.id == CurriculumEditor.user_id)
        .filter(CurriculumEditor.curriculum_id == cur.id)
        .order_by(User.email.asc())
        .all()
    )

    return render_template(
        "author/curriculum_edit.html",
        curriculum=cur,
        items=items,
        lessons=lessons,
        editors=editors,
    )


@bp.post("/curriculum/<curriculum_id>/editors/invite")
@login_required
def curriculum_editor_invite(curriculum_id: str):
    _require_author()
    from app.models import Curriculum, CurriculumEditor, User

    cur = Curriculum.query.get_or_404(curriculum_id)
    _require_curriculum_perm(cur.id, need_manage=True)

    email = (request.form.get("email") or "").strip().lower()
    if not email:
        flash("Enter an email.", "error")
        return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))

    u = User.query.filter(db.func.lower(User.email) == email).one_or_none()
    if not u:
        flash("No user found with that email.", "error")
        return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))

    row = CurriculumEditor.query.filter_by(curriculum_id=cur.id, user_id=u.id).one_or_none()
    if row:
        row.can_edit = True
        row.invited_by_user_id = current_user.id
    else:
        db.session.add(CurriculumEditor(
            curriculum_id=cur.id,
            user_id=u.id,
            can_edit=True,
            invited_by_user_id=current_user.id,
        ))

    db.session.commit()
    flash(f"Granted edit access to {u.email}.", "success")
    return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))


@bp.post("/curriculum/<curriculum_id>/editors/approve")
@login_required
def curriculum_editor_approve(curriculum_id: str):
    _require_author()
    from app.models import Curriculum, CurriculumEditor

    cur = Curriculum.query.get_or_404(curriculum_id)
    _require_curriculum_perm(cur.id, need_manage=True)

    user_id = (request.form.get("user_id") or "").strip()
    row = CurriculumEditor.query.filter_by(curriculum_id=cur.id, user_id=user_id).one_or_none()
    if not row:
        flash("Request not found.", "error")
        return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))

    row.can_edit = True
    row.invited_by_user_id = current_user.id
    db.session.commit()
    flash("Edit access approved.", "success")
    return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))


@bp.post("/curriculum/<curriculum_id>/editors/remove")
@login_required
def curriculum_editor_remove(curriculum_id: str):
    _require_author()
    from app.models import Curriculum, CurriculumEditor

    cur = Curriculum.query.get_or_404(curriculum_id)
    _require_curriculum_perm(cur.id, need_manage=True)

    user_id = (request.form.get("user_id") or "").strip()
    row = CurriculumEditor.query.filter_by(curriculum_id=cur.id, user_id=user_id).one_or_none()
    if row:
        db.session.delete(row)
        db.session.commit()

    flash("Editor removed.", "success")
    return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))





ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}

def _allowed_image(filename: str) -> bool:
    ext = (filename.rsplit(".", 1)[-1] or "").lower()
    return ext in ALLOWED_EXTS

def _save_curriculum_cover(curriculum_id: str, file_storage) -> str:
    # /app/app/static/user_uploads/curriculum_covers/<curriculum_id>/cover.<ext>
    ext = (file_storage.filename.rsplit(".", 1)[-1] or "").lower()
    folder = os.path.join(current_app.root_path, "static", "user_uploads", "curriculum_covers", curriculum_id)
    os.makedirs(folder, exist_ok=True)

    filename = secure_filename(f"cover.{ext}")
    abs_path = os.path.join(folder, filename)
    file_storage.save(abs_path)

    # Cache-bust so users see the new image immediately
    rel = f"user_uploads/curriculum_covers/{curriculum_id}/{filename}"
    return url_for("static", filename=rel, v=str(int(time.time())))



@bp.post("/curriculum/<curriculum_id>/edit")
@login_required
def curriculum_edit_post(curriculum_id: str):
    _require_author()
    from app.models import Curriculum, CurriculumItem
    from flask import abort

    cur = Curriculum.query.get_or_404(curriculum_id)
    try:
        _require_curriculum_perm(cur.id, need_edit=True)
    except PermissionError:
        abort(403)

    # 1. Handle Metadata (Title/Desc) if provided in the main save
    new_title = (request.form.get("title_meta") or "").strip()
    if new_title:
        cur.title = new_title
    cur.description = (request.form.get("description_meta") or "").strip() or cur.description

    # 2. Handle Publish / Unpublish
    action = (request.form.get("publish_action") or "").strip().lower()
    if action == "publish":
        cur.is_published = True
        if not cur.published_at:
            cur.published_at = datetime.utcnow()
    elif action == "unpublish":
        cur.is_published = False
        cur.published_at = None

    # 3. Items Update (SAFEGUARD: Only wipe if data is present)
    item_type = request.form.getlist("item_type")
    if item_type:
        CurriculumItem.query.filter_by(curriculum_id=cur.id).delete()
        db.session.flush()

        pos = 0
        phase_title = request.form.getlist("phase_title")
        lesson_id = request.form.getlist("lesson_id")
        note = request.form.getlist("note")
        repeat = request.form.getlist("repeat")

        for i, t in enumerate(item_type):
            t = (t or "").strip().lower()
            if t == "phase":
                title = (phase_title[i] if i < len(phase_title) else "").strip() or "Phase"
                db.session.add(CurriculumItem(curriculum_id=cur.id, position=pos, item_type="phase", phase_title=title))
                pos += 1
            elif t == "lesson":
                lid = (lesson_id[i] if i < len(lesson_id) else "").strip() or None
                if not lid: continue
                n = 1
                try:
                    n = max(1, min(50, int((repeat[i] if i < len(repeat) else "1") or "1")))
                except: n = 1
                n_note = (note[i] if i < len(note) else "").strip() or None
                for _ in range(n):
                    db.session.add(CurriculumItem(curriculum_id=cur.id, position=pos, item_type="lesson", lesson_id=lid, note=n_note))
                    pos += 1

    # 4. Handle Cover
    cover = request.files.get("cover_image")
    if cover and cover.filename:
        cur.cover_image_url = _save_curriculum_cover(cur.id, cover)

    db.session.commit()
    flash("Curriculum saved.", "success")
    return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))

# @bp.post("/curriculum/<curriculum_id>/edit")
# @login_required
# def curriculum_edit_post(curriculum_id: str):
#     _require_author()
#     from app.models import Curriculum, CurriculumItem
#     from flask import abort
#
#     cur = Curriculum.query.get_or_404(curriculum_id)
#
#     # Match GET permissions (important)
#     try:
#         _require_curriculum_perm(cur.id, need_edit=True)
#     except PermissionError:
#         abort(403)
#
#     # Handle Publish / Unpublish
#     action = (request.form.get("publish_action") or "").strip().lower()
#     if action == "publish":
#         cur.is_published = True
#         if not cur.published_at:
#             cur.published_at = _dt.utcnow()
#     elif action == "unpublish":
#         cur.is_published = False
#         cur.published_at = None
#
#     # Items come in as parallel arrays
#     item_type = request.form.getlist("item_type")
#     phase_title = request.form.getlist("phase_title")
#     lesson_id = request.form.getlist("lesson_id")
#     note = request.form.getlist("note")
#     repeat = request.form.getlist("repeat")
#
#     # wipe + rebuild (fast + consistent)
#     CurriculumItem.query.filter_by(curriculum_id=cur.id).delete()
#     db.session.flush()
#
#     pos = 0
#     for i, t in enumerate(item_type):
#         t = (t or "").strip().lower()
#
#         if t == "phase":
#             title = (phase_title[i] if i < len(phase_title) else "").strip()
#             if not title:
#                 title = "Phase"
#             db.session.add(CurriculumItem(
#                 curriculum_id=cur.id,
#                 position=pos,
#                 item_type="phase",
#                 phase_title=title,
#             ))
#             pos += 1
#
#         elif t == "lesson":
#             lid = (lesson_id[i] if i < len(lesson_id) else "").strip() or None
#             if not lid:
#                 continue
#             n = 1
#             try:
#                 n = max(1, min(50, int((repeat[i] if i < len(repeat) else "1") or "1")))
#             except Exception:
#                 n = 1
#             n_note = (note[i] if i < len(note) else "").strip() or None
#
#             for _ in range(n):
#                 db.session.add(CurriculumItem(
#                     curriculum_id=cur.id,
#                     position=pos,
#                     item_type="lesson",
#                     lesson_id=lid,
#                     note=n_note,
#                 ))
#                 pos += 1
#
#     cover = request.files.get("cover_image")
#     if cover and cover.filename:
#         if not _allowed_image(cover.filename):
#             flash("Cover image must be png/jpg/jpeg/webp/gif.", "error")
#             return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))
#
#         cur.cover_image_url = _save_curriculum_cover(cur.id, cover)
#
#     db.session.commit()
#     flash("Curriculum saved.", "success")
#     return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))


@bp.post("/curriculum/<curriculum_id>/cover/remove")
@login_required
def curriculum_cover_remove(curriculum_id):
    _require_author()
    cur = Curriculum.query.get_or_404(curriculum_id)
    cur.cover_image_url = None
    db.session.commit()
    flash("Cover image removed.", "success")
    return redirect(url_for("author.curriculum_edit", curriculum_id=curriculum_id))




# @bp.post("/curriculum/<curriculum_id>/update-title")
# @login_required
# def curriculum_update_title(curriculum_id):
#     # Use our updated permission helper!
#     _require_curriculum_perm(curriculum_id, need_edit=True)
#
#     cur = Curriculum.query.get_or_404(curriculum_id)
#     new_title = request.form.get("title", "").strip()
#
#     if not new_title:
#         flash("Title cannot be empty", "error")
#         return redirect(url_for("author.curriculum_edit", curriculum_id=curriculum_id))
#
#     cur.title = new_title
#     db.session.commit()
#
#     flash("Title updated!", "success")
#     return redirect(url_for("author.curriculum_edit", curriculum_id=curriculum_id))
#


@bp.post("/curriculum/<curriculum_id>/update-settings")
@login_required
def curriculum_update_settings(curriculum_id):
    # Use your new permission companion
    _require_curriculum_perm(curriculum_id, need_edit=True)

    cur = Curriculum.query.get_or_404(curriculum_id)
    new_title = request.form.get("title", "").strip()
    new_desc = request.form.get("description", "").strip()

    if not new_title:
        flash("Title cannot be empty", "error")
        return redirect(url_for("author.curriculum_edit", curriculum_id=curriculum_id))

    cur.title = new_title
    cur.description = new_desc
    db.session.commit()

    flash("Curriculum settings updated!", "success")
    return redirect(url_for("author.curriculum_edit", curriculum_id=curriculum_id))



from uuid import uuid4
from datetime import datetime as _dt

@bp.post("/curriculum/<curriculum_id>/orders")
@login_required
def place_order(curriculum_id: str):
    """
    JSON body:
      {
        "side": "bid" | "ask",
        "qty_shares": 5,
        "price_an": 1.23    # optional alternative to price_ticks_per_share
        "price_ticks_per_share": 1230
      }
    """
    from app.access_ledger.service import (
        get_an_asset, get_curriculum_share_asset,
        get_or_create_user_wallet, get_or_create_system_account,
        post_access_txn, EntrySpec, AN_SCALE,
    )
    from app.models import MarketOrder, Curriculum

    Curriculum.query.filter_by(id=curriculum_id).one_or_none() or (lambda: (_ for _ in ()).throw(ValueError("bad curriculum_id")))()

    payload = request.get_json(force=True) or {}
    side = (payload.get("side") or "").strip().lower()
    qty = int(payload.get("qty_shares") or 0)

    if side not in ("bid", "ask"):
        return {"error": "side must be bid or ask"}, 400
    if qty <= 0:
        return {"error": "qty_shares must be > 0"}, 400

    if payload.get("price_ticks_per_share") is not None:
        price_ticks = int(payload["price_ticks_per_share"])
    else:
        # price_an is in AN, convert to ticks
        price_an = payload.get("price_an")
        if price_an is None:
            return {"error": "provide price_ticks_per_share or price_an"}, 400
        price_ticks = int(round(float(price_an) * AN_SCALE))

    if price_ticks <= 0:
        return {"error": "price must be > 0"}, 400

    an = get_an_asset()
    share_asset = get_curriculum_share_asset(curriculum_id)

    user_wallet = get_or_create_user_wallet(current_user.id)
    escrow = get_or_create_system_account("escrow_pool")  # shared escrow account for all markets

    # Lock funds into escrow
    if side == "bid":
        locked_an = price_ticks * qty
        txn_id = post_access_txn(
            event_type="market_place_bid",
            idempotency_key=f"market_place_bid:{uuid4().hex}",
            actor_user_id=current_user.id,
            context_type="curriculum",
            context_id=curriculum_id,
            entries=[
                EntrySpec(account_id=user_wallet.id, asset_id=an.id, delta=-locked_an),
                EntrySpec(account_id=escrow.id, asset_id=an.id, delta=+locked_an),
            ],
        )
        order = MarketOrder(
            curriculum_id=curriculum_id,
            user_id=current_user.id,
            side="bid",
            status="open",
            price_ticks_per_share=price_ticks,
            qty_shares=qty,
            remaining_shares=qty,
            locked_an_ticks=locked_an,
            locked_shares=0,
        )
    else:
        locked_sh = qty
        txn_id = post_access_txn(
            event_type="market_place_ask",
            idempotency_key=f"market_place_ask:{uuid4().hex}",
            actor_user_id=current_user.id,
            context_type="curriculum",
            context_id=curriculum_id,
            entries=[
                EntrySpec(account_id=user_wallet.id, asset_id=share_asset.id, delta=-locked_sh),
                EntrySpec(account_id=escrow.id, asset_id=share_asset.id, delta=+locked_sh),
            ],
        )
        order = MarketOrder(
            curriculum_id=curriculum_id,
            user_id=current_user.id,
            side="ask",
            status="open",
            price_ticks_per_share=price_ticks,
            qty_shares=qty,
            remaining_shares=qty,
            locked_an_ticks=0,
            locked_shares=locked_sh,
        )

    db.session.add(order)
    db.session.commit()

    # Matching engine (simple + deterministic)
    _match_curriculum_orders(curriculum_id, new_order_id=order.id)

    return {"ok": True, "order_id": order.id, "lock_txn_id": txn_id}



from datetime import datetime

@bp.post("/curriculum/<curriculum_id>/archive")
@login_required
def curriculum_archive(curriculum_id: str):
    _require_author()
    cur = Curriculum.query.get_or_404(curriculum_id)

    try:
        _require_curriculum_perm(cur.id, need_manage=True)
    except PermissionError:
        abort(403)

    if not cur.is_archived:
        cur.is_archived = True
        cur.archived_at = datetime.utcnow()
        db.session.commit()
        flash("Curriculum archived. It is now hidden from users.", "success")
    else:
        flash("Curriculum is already archived.", "info")

    return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))


@bp.post("/curriculum/<curriculum_id>/unarchive")
@login_required
def curriculum_unarchive(curriculum_id: str):
    _require_author()
    cur = Curriculum.query.get_or_404(curriculum_id)

    try:
        _require_curriculum_perm(cur.id, need_manage=True)
    except PermissionError:
        abort(403)

    if cur.is_archived:
        cur.is_archived = False
        cur.archived_at = None
        db.session.commit()
        flash("Curriculum restored.", "success")
    else:
        flash("Curriculum is not archived.", "info")

    return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))


# app/author/routes.py

def process_lesson_import(lesson_json_data: dict, assets_zip_stream=None) -> Lesson:
    """Core logic to create/update a lesson from JSON data and an optional ZIP of assets."""
    payload = _validate_lesson_payload(lesson_json_data)
    code = payload["code"].strip()

    # Upsert Lesson metadata
    lesson = Lesson.query.filter_by(code=code).one_or_none()
    if not lesson:
        lesson = Lesson(code=code, created_by_user_id=current_user.id)
        db.session.add(lesson)

    lesson.title = payload["title"].strip()
    lesson.description = payload.get("description")
    lesson.language_code = payload.get("language_code")
    lesson.is_published = bool(payload.get("is_published"))
    db.session.flush()

    # Clear old blocks
    LessonBlock.query.filter_by(lesson_id=lesson.id).delete()

    # Handle Assets
    extracted = {}
    if assets_zip_stream:
        LessonAsset.query.filter_by(lesson_id=lesson.id).delete()
        db.session.flush()
        extracted = _extract_assets_zip(assets_zip_stream, lesson.id)

    # Register Assets and Blocks... (Keep your existing logic from import_post here)
    # ...

    return lesson



def _create_block_records(lesson_id, blocks_data, asset_map):
    """
    Iterates through lesson block data and creates LessonBlock records.
    Uses asset_map (filename -> asset_id) to potentially resolve references.
    """
    for idx, b_data in enumerate(blocks_data):
        b_type = b_data.get("type")
        payload = b_data.get("payload", {})

        # If the block references an asset (e.g., audio_asset, video_asset),
        # we ensure the 'ref' exists in our newly uploaded assets
        if b_type in ("audio_asset", "video_asset") and "ref" in payload:
            ref = payload["ref"]
            # We strip 'assets/' prefix if the map uses raw filenames,
            # or keep it if your map uses the full ref path
            filename = ref.replace("assets/", "")
            if filename not in asset_map:
                print(f"Warning: Block {idx} references missing asset {ref}")

        # Create the block record
        new_block = LessonBlock(
            lesson_id=lesson_id,
            position=idx,
            type=b_type,
            payload_json=payload
        )
        db.session.add(new_block)

def _process_dropped_files(json_file, asset_files, user_id):
    """
    Helper: Handles creating/updating a lesson from a dropped lesson.json
    and a list of asset files. Does NOT commit the session.
    """
    # 1. Load and validate JSON
    try:
        payload = _load_json_file(json_file)
        payload = _validate_lesson_payload(payload)
    except ValueError as e:
        raise ValueError(str(e))

    code = payload["code"].strip()

    # 2. Get or create Lesson
    lesson = Lesson.query.filter_by(code=code).one_or_none()
    if not lesson:
        lesson = Lesson(code=code, created_by_user_id=user_id)
        db.session.add(lesson)

    # 3. Update metadata
    lesson.title = payload["title"].strip()
    lesson.description = payload.get("description")
    lesson.language_code = payload.get("language_code")
    lesson.is_published = bool(payload.get("is_published"))

    # Flush to get lesson.id for asset paths
    db.session.flush()

    # 4. Clear existing blocks and assets so we can rebuild them
    LessonBlock.query.filter_by(lesson_id=lesson.id).delete()
    LessonAsset.query.filter_by(lesson_id=lesson.id).delete()

    # 5. Setup asset directory on disk
    assets_dir = os.path.join(current_app.instance_path, 'lesson_assets', str(lesson.id))
    if os.path.exists(assets_dir):
        shutil.rmtree(assets_dir)
    os.makedirs(assets_dir, exist_ok=True)

    # 6. Process and save all asset files
    asset_map = {}  # Maps filename -> new asset_id

    for f_storage in asset_files:
        filename = secure_filename(f_storage.filename)
        if not filename:
            continue

        save_path = os.path.join(assets_dir, filename)
        f_storage.save(save_path)

        # Determine asset type roughly based on extension
        ext = os.path.splitext(filename)[1].lower()
        asset_type = 'other'
        if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            asset_type = 'image'
        elif ext in ['.mp3', '.wav', '.ogg', '.m4a']:
            asset_type = 'audio'
        elif ext in ['.mp4', '.mov', '.webm']:
            asset_type = 'video'

        asset = LessonAsset(
            lesson_id=lesson.id,
            filename=filename,
            storage_path=os.path.join('lesson_assets', str(lesson.id), filename),
            asset_type=asset_type,
            created_by_user_id=user_id
        )
        db.session.add(asset)
        db.session.flush()  # Flush to get the new asset.id
        asset_map[filename] = asset.id

    # 7. Create blocks using the asset map to link files
    blocks_data = payload.get("blocks", [])
    _create_block_records(lesson.id, blocks_data, asset_map)

    return lesson


# app/author/routes.py
from sqlalchemy import func  # Ensure this is imported at the top!


@bp.post("/curriculum/<curriculum_id>/drop_import")
@login_required
def curriculum_drop_import(curriculum_id: str):
    _require_curriculum_perm(curriculum_id, need_edit=True)

    # 1. Capture the JSON and the list of individual files
    lesson_json_fs = request.files.get("lesson_json")
    asset_files = request.files.getlist("asset_files")  # Note: getlist for multiple files

    if not lesson_json_fs:
        return jsonify({"error": "Missing lesson.json"}), 400

    try:
        # Load and validate the lesson metadata
        payload = _load_json_file(lesson_json_fs)
        payload = _validate_lesson_payload(payload)
        code = payload["code"].strip()

        # 2. Create or Update the Lesson
        lesson = Lesson.query.filter_by(code=code).one_or_none()
        if not lesson:
            lesson = Lesson(code=code, created_by_user_id=current_user.id)
            db.session.add(lesson)

        lesson.title = payload["title"].strip()
        lesson.description = payload.get("description")
        lesson.is_published = bool(payload.get("is_published"))
        db.session.flush()

        # 3. Clean up old blocks and assets
        LessonBlock.query.filter_by(lesson_id=lesson.id).delete()
        LessonAsset.query.filter_by(lesson_id=lesson.id).delete()

        # 4. Save individual asset files to disk
        assets_dir = _assets_root() / lesson.id / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        for f in asset_files:
            if not f.filename: continue
            fname = secure_filename(f.filename)
            f.save(assets_dir / fname)

            # Register asset in DB so the lesson can find it
            db.session.add(LessonAsset(
                lesson_id=lesson.id,
                ref=f"assets/{fname}",
                storage_path=str(assets_dir / fname),
                content_type=f.mimetype
            ))

        # 5. Rebuild Blocks
        for idx, b in enumerate(payload["blocks"]):
            db.session.add(LessonBlock(
                lesson_id=lesson.id,
                position=idx,
                type=b["type"],
                payload_json=b["payload"]
            ))

        # 6. Auto-link to the end of the Curriculum
        last_pos = (db.session.query(func.max(CurriculumItem.position))
                    .filter_by(curriculum_id=curriculum_id).scalar())
        next_pos = (last_pos + 1) if last_pos is not None else 0

        db.session.add(CurriculumItem(
            curriculum_id=curriculum_id,
            position=next_pos,
            item_type="lesson",
            lesson_id=lesson.id
        ))

        db.session.commit()
        return jsonify({"success": True, "lesson_id": lesson.id, "title": lesson.title})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500




@bp.post("/orders/<int:order_id>/cancel")
@login_required
def cancel_order(order_id: int):
    from app.access_ledger.service import (
        get_an_asset, get_curriculum_share_asset,
        get_or_create_user_wallet, get_or_create_system_account,
        post_access_txn, EntrySpec,
    )
    from app.models import MarketOrder

    o = MarketOrder.query.filter_by(id=order_id).one_or_none()
    if not o:
        return {"error": "order not found"}, 404
    if o.user_id != current_user.id:
        return {"error": "not your order"}, 403
    if o.status != "open" and o.status != "partial":
        return {"error": f"cannot cancel status={o.status}"}, 400

    an = get_an_asset()
    share_asset = get_curriculum_share_asset(o.curriculum_id)
    user_wallet = get_or_create_user_wallet(current_user.id)
    escrow = get_or_create_system_account("escrow_pool")

    entries = []
    if o.side == "bid":
        # refund remaining locked AN (whatever is still locked)
        if o.locked_an_ticks > 0:
            entries = [
                EntrySpec(account_id=escrow.id, asset_id=an.id, delta=-int(o.locked_an_ticks)),
                EntrySpec(account_id=user_wallet.id, asset_id=an.id, delta=+int(o.locked_an_ticks)),
            ]
    else:
        if o.locked_shares > 0:
            entries = [
                EntrySpec(account_id=escrow.id, asset_id=share_asset.id, delta=-int(o.locked_shares)),
                EntrySpec(account_id=user_wallet.id, asset_id=share_asset.id, delta=+int(o.locked_shares)),
            ]

    if entries:
        post_access_txn(
            event_type="market_cancel_order",
            idempotency_key=f"market_cancel:{order_id}:{uuid4().hex}",
            actor_user_id=current_user.id,
            context_type="curriculum",
            context_id=o.curriculum_id,
            entries=entries,
        )

    o.status = "canceled"
    o.canceled_at = _dt.utcnow()
    o.remaining_shares = 0
    o.locked_an_ticks = 0
    o.locked_shares = 0
    db.session.commit()

    return {"ok": True}


def _match_curriculum_orders(curriculum_id: str, *, new_order_id: int | None = None) -> None:
    """
    Very simple matcher:
      - Crosses bids/asks if prices overlap
      - Price rule: maker price (the existing resting order)
      - Uses escrow account to deliver shares/AN
      - Updates MarketOrder remaining + locked fields
      - Records MarketTrade
    """
    from app.access_ledger.service import (
        get_an_asset, get_curriculum_share_asset,
        get_or_create_user_wallet, get_or_create_system_account,
        post_access_txn, EntrySpec,
    )
    from app.models import MarketOrder, MarketTrade
    from sqlalchemy import and_

    an = get_an_asset()
    share_asset = get_curriculum_share_asset(curriculum_id)
    escrow = get_or_create_system_account("escrow_pool")

    while True:
        # best bid, best ask
        best_bid = (MarketOrder.query
            .filter_by(curriculum_id=curriculum_id)
            .filter(MarketOrder.side == "bid")
            .filter(MarketOrder.status.in_(("open","partial")))
            .filter(MarketOrder.remaining_shares > 0)
            .order_by(MarketOrder.price_ticks_per_share.desc(), MarketOrder.created_at.asc(), MarketOrder.id.asc())
            .with_for_update()
            .first())

        best_ask = (MarketOrder.query
            .filter_by(curriculum_id=curriculum_id)
            .filter(MarketOrder.side == "ask")
            .filter(MarketOrder.status.in_(("open","partial")))
            .filter(MarketOrder.remaining_shares > 0)
            .order_by(MarketOrder.price_ticks_per_share.asc(), MarketOrder.created_at.asc(), MarketOrder.id.asc())
            .with_for_update()
            .first())

        if not best_bid or not best_ask:
            db.session.commit()
            return

        # no cross
        if best_ask.price_ticks_per_share > best_bid.price_ticks_per_share:
            db.session.commit()
            return

        # maker price = resting order price (one that is NOT the new order, if provided)
        if new_order_id is not None and best_bid.id == new_order_id and best_ask.id != new_order_id:
            trade_price = int(best_ask.price_ticks_per_share)
        elif new_order_id is not None and best_ask.id == new_order_id and best_bid.id != new_order_id:
            trade_price = int(best_bid.price_ticks_per_share)
        else:
            # default: seller price (ask) when ambiguous
            trade_price = int(best_ask.price_ticks_per_share)

        qty = int(min(best_bid.remaining_shares, best_ask.remaining_shares))
        if qty <= 0:
            db.session.commit()
            return

        buyer_wallet = get_or_create_user_wallet(best_bid.user_id)
        seller_wallet = get_or_create_user_wallet(best_ask.user_id)

        # Buyer previously locked at bid.price; if trade executes cheaper, refund the difference
        bid_price = int(best_bid.price_ticks_per_share)
        refund = max(0, bid_price - trade_price) * qty

        # Total AN leaving escrow for this fill from the bid lock = bid_price * qty
        escrow_out = bid_price * qty
        seller_get = trade_price * qty

        entries = [
            # AN leg
            EntrySpec(account_id=escrow.id, asset_id=an.id, delta=-escrow_out),
            EntrySpec(account_id=seller_wallet.id, asset_id=an.id, delta=+seller_get),
        ]
        if refund > 0:
            entries.append(EntrySpec(account_id=buyer_wallet.id, asset_id=an.id, delta=+refund))

        # Share leg
        entries += [
            EntrySpec(account_id=escrow.id, asset_id=share_asset.id, delta=-qty),
            EntrySpec(account_id=buyer_wallet.id, asset_id=share_asset.id, delta=+qty),
        ]

        ledger_txn_id = post_access_txn(
            event_type="market_fill",
            idempotency_key=f"market_fill:{best_bid.id}:{best_ask.id}:{uuid4().hex}",
            context_type="curriculum",
            context_id=curriculum_id,
            entries=entries,
        )

        # Update orders
        best_bid.remaining_shares -= qty
        best_ask.remaining_shares -= qty

        best_bid.locked_an_ticks -= escrow_out
        best_ask.locked_shares -= qty

        best_bid.status = "filled" if best_bid.remaining_shares == 0 else "partial"
        best_ask.status = "filled" if best_ask.remaining_shares == 0 else "partial"
        if best_bid.remaining_shares == 0:
            best_bid.filled_at = _dt.utcnow()
        if best_ask.remaining_shares == 0:
            best_ask.filled_at = _dt.utcnow()

        db.session.add(MarketTrade(
            curriculum_id=curriculum_id,
            buy_order_id=best_bid.id,
            sell_order_id=best_ask.id,
            buyer_id=best_bid.user_id,
            seller_id=best_ask.user_id,
            price_ticks_per_share=trade_price,
            qty_shares=qty,
            ledger_txn_id=ledger_txn_id,
        ))

        db.session.commit()
