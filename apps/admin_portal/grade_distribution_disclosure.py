"""Decimal range validation and the public release serialization boundary."""
import re
from decimal import Decimal

from django.core.exceptions import ValidationError


CELL_STARTS = tuple(map(Decimal, ("0", "50", "75", "90", "95", "97")))
POLICY_VERSION = "saved-period-97-100-v1"
HOLD_MESSAGE = (
    "Only approved frozen 97–100 saved Period Grade releases show counts. "
    "Missing, unpublished, changed or unsafe releases and all other measures/ranges are withheld."
)


def parse_bounds(query):
    values = []
    for name, default in (("grade_min", "97"), ("grade_max", "100")):
        raw = query.get(name, default)
        if len(query.getlist(name)) > 1:
            raise ValidationError("Supply each Grade Range bound only once.")
        raw = raw.strip()
        if len(raw) > 32 or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", raw):
            raise ValidationError("Grade Range must contain finite decimal numbers from 0 to 100.")
        value = Decimal(raw)
        if not value.is_finite() or not 0 <= value <= 100:
            raise ValidationError("Grade Range must be between 0 and 100.")
        values.append(value)
    if values[0] > values[1]:
        raise ValidationError("Minimum Grade must not exceed Maximum Grade.")
    return tuple(values)


def decimal_label(value):
    # Avoid context-dependent Decimal.normalize() rounding long valid bounds.
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def range_label(bounds):
    return "Grades " + "–".join(decimal_label(value) for value in bounds)


def range_is_cell_union(bounds):
    # Cells are [start, next_start), with the final cell [97, 100]. Since
    # requested upper bounds are inclusive and Quiz values are not rounded,
    # only a union ending at 100 is exactly expressible by these boundaries.
    return bounds[0] in CELL_STARTS and bounds[1] == Decimal("100")


def partition_counts(frequency):
    """Server-only candidate cells, including out-of-scale complements."""
    counts = [0] * (len(CELL_STARTS) + 2)
    for item in frequency:
        value = Decimal(item["grade"])
        index = (0 if value < 0 else len(counts) - 1 if value > 100 else
                 1 + max(i for i, start in enumerate(CELL_STARTS) if value >= start))
        counts[index] += item["count"]
    return counts


def candidate_range(frequency, bounds):
    """Internal exact arithmetic; never a disclosure authorization."""
    total = sum(item["count"] for item in frequency)
    count = sum(item["count"] for item in frequency
                if bounds[0] <= Decimal(item["grade"]) <= bounds[1])
    return count, total


def public_measure(measure, bounds, *, historical=False, frozen=None):
    """Erase live numerical analytics; admit only the verified frozen aggregate."""
    keys = ("dataset_id", "offering_id", "physical_period_id", "activity_id",
            "selection_status", "gradebook_context", "label", "unit", "group",
            "graded_count", "eligible_count", "finalized_count", "unfinalized_count", "status")
    result = {key: measure[key] for key in keys if key in measure}
    result.update(
        analytics_state="privacy_suppressed", privacy_suppressed=True,
        privacy_reason="release_history_unverified", privacy_message=HOLD_MESSAGE,
        range_label=range_label(bounds), grade_min=decimal_label(bounds[0]),
        grade_max=decimal_label(bounds[1]), range_state="withheld",
        range_count=None, range_denominator=None, range_percentage=None,
        average=None, minimum=None, maximum=None, exact_100_count=None,
        exact_100_percentage=None, all_100=None, high_grade_breakdown=[],
        frequency=[], distribution=[], disclosure_policy=POLICY_VERSION,
    )
    if frozen is not None:
        result.update(frozen, range_state="disclosed", privacy_reason="", privacy_message="Approved immutable saved Period Grade release.")
    # Do not expose value-dependent lineage/equivalence/availability decisions
    # through diagnostic prose while the whole release remains unauthorized.
    # Coverage is retained by the existing contract; these notices use only it.
    if 0 < measure.get("graded_count", 0) < 5:
        result["source_notice"] = "Detailed grade analytics are unavailable because fewer than five students have saved grades for this selection."
    elif not measure.get("graded_count"):
        result["source_notice"] = "No eligible saved values are available for this measure."
    if measure.get("calculation_note"):
        result["calculation_note"] = (
            "Historical Quiz calculations have no immutable original formula snapshot."
            if historical else "Quiz averages use current saved Gradebook grades and configuration, including the configured base; not an immutable finalized snapshot."
        )
    if historical:
        result["history_notice"] = "Historical analytics use retained saved records; original roster, formula and score-revision snapshots are unavailable."
    return result
