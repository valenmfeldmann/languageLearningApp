# app/lessons/catalog.py

LESSONS = {
    "spanish_verbs_1": {
        "code": "spanish_verbs_1",
        "title": "Spanish Verbs 1: Ser vs Estar (micro intro)",
        "reward_notes": 5,
        "blocks": [
            {"type": "text", "text": "Goal: understand when to use **ser** vs **estar** (very roughly)."},
            {"type": "text", "text": "**ser** ≈ identity / inherent traits. **estar** ≈ state / location."},
        ],
    }
}

def get_lesson(code: str):
    return LESSONS.get(code)
