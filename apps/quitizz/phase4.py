"""Launch policy, read-only awards and session-locked navigation helpers."""
from fractions import Fraction

from django.core.exceptions import ValidationError
from django.db.models import Count, Sum

from .models import QuiTizzResponse


SCREENS = ("ANSWER", "FASTEST_FIVE", "HIGHEST_SCORES")
DEFAULTS = {"show_fastest": True, "show_scores": True, "final_fastest": False,
            "answer_seconds": 2, "fastest_seconds": 5, "scores_seconds": 5}


def launch_policy(options=None):
    from .automation import POLICY
    values = {**DEFAULTS, **(options or {})}
    if set(values) != set(DEFAULTS):
        raise ValidationError("Unknown presentation setting.")
    for key in ("show_fastest", "show_scores", "final_fastest"):
        if type(values[key]) is not bool:
            raise ValidationError("Select valid presentation options.")
    for key in ("answer_seconds", "fastest_seconds", "scores_seconds"):
        if type(values[key]) is not int or not 1 <= values[key] <= 60:
            raise ValidationError("Presentation durations must be between 1 and 60 seconds.")
    return {**POLICY, "version": 2, "presentation": values,
            "awards": {"minimum_correct_percent": 50, "tie_policy": "count_points_identifier"}}


def policy(session):
    from .automation import POLICY
    saved = session.automation_policy_snapshot or {}
    # Read without ever filling/mutating the persisted historical JSON.
    result = {**POLICY, **saved}
    result["presentation"] = {**DEFAULTS, **saved.get("presentation", {})} if result["version"] == 2 else {
        **DEFAULTS, "show_fastest": False, "show_scores": True, "final_fastest": False}
    return result


def modern(session):
    return policy(session)["version"] == 2


def screens(session):
    options = policy(session)["presentation"]
    return ["ANSWER"] + (["FASTEST_FIVE"] if options["show_fastest"] else []) + (["HIGHEST_SCORES"] if options["show_scores"] else [])


def screen_seconds(session, screen="ANSWER"):
    values = policy(session)
    if values["version"] != 2:
        return values["results_seconds"]
    return values["presentation"][{"ANSWER": "answer_seconds", "FASTEST_FIVE": "fastest_seconds", "HIGHEST_SCORES": "scores_seconds"}[screen]]


def pending_question(session):
    """Caller holds the session row lock. Never reorder snapshot positions."""
    pending = session.questions.filter(opened_at__isnull=True)
    if session.upcoming_question_id:
        selected = pending.filter(pk=session.upcoming_question_id).first()
        if selected:
            return selected
    return pending.order_by("position", "pk").first()


def fastest(question):
    if not question or not question.revealed_at:
        return []
    rows = question.responses.filter(is_correct=True, participant__removed_at__isnull=True).order_by(
        "elapsed_ms", "received_at", "pk").values("participant__nickname", "elapsed_ms")[:5]
    return [{"rank": rank, "nickname": row["participant__nickname"], "elapsed_ms": row["elapsed_ms"]}
            for rank, row in enumerate(rows, 1)]


def final_award(session):
    if session.status != "COMPLETED" or not policy(session)["presentation"]["final_fastest"]:
        return None
    total = session.questions.count()
    rows = list(QuiTizzResponse.objects.filter(session_question__session=session, participant__removed_at__isnull=True,
        is_correct=True).values("participant__public_id", "participant__nickname", "participant__total_score").annotate(
            correct=Count("pk"), elapsed=Sum("elapsed_ms")))
    eligible = [row for row in rows if row["correct"] > 0 and row["correct"] * 2 >= total]
    if not eligible:
        return None
    winner = min(eligible, key=lambda row: (Fraction(row["elapsed"], row["correct"]), -row["correct"],
        -row["participant__total_score"], str(row["participant__public_id"])))
    # Fraction is used for ordering; round only the display, using integer arithmetic.
    hundredths = (winner["elapsed"] * 100 + winner["correct"] // 2) // winner["correct"]
    return {"nickname": winner["participant__nickname"], "correct_count": winner["correct"],
            "question_count": total, "average_ms": f"{hundredths // 100}.{hundredths % 100:02d}"}


def navigator(session):
    rows = list(session.questions.order_by("position").values("pk", "public_id", "position", "prompt", "opened_at", "revealed_at"))
    return {"upcoming_question": str(next((row["public_id"] for row in rows if row["pk"] == session.upcoming_question_id), "")),
            "pending_count": sum(row["opened_at"] is None for row in rows),
            "completed_count": sum(row["revealed_at"] is not None for row in rows),
            "navigator": [{"id": str(row["public_id"]), "position": row["position"], "preview": row["prompt"][:120],
                "status": "Completed" if row["revealed_at"] else "Current" if row["position"] == session.current_position and row["opened_at"] else "Pending",
                "can_select": row["opened_at"] is None, "can_review": row["revealed_at"] is not None} for row in rows]}
