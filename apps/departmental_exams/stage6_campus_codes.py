from __future__ import annotations


STAGE6_CAMPUS_CODE_ALIASES = {
    "CUBAO": "CUBAO",
    "NCBA-CUBAO": "CUBAO",
    "NCBA-01": "CUBAO",
    "FAIRVIEW": "FAIRVIEW",
    "NCBA-FAIRVIEW": "FAIRVIEW",
    "NCBA-02": "FAIRVIEW",
    "TAYTAY": "TAYTAY",
    "NCBA-TAYTAY": "TAYTAY",
    "NCBA-03": "TAYTAY",
}


class Stage6CampusCodeAmbiguity(ValueError):
    """Distinct Campus rows resolve to the same Stage 6 allocation key."""


def canonicalize_stage6_campus_code(value) -> str:
    normalized = str(value or "").strip().upper()
    return STAGE6_CAMPUS_CODE_ALIASES.get(normalized, normalized)


def canonicalize_participating_campus_rows(campus_rows) -> tuple[str, ...]:
    """Return unique canonical keys while preserving distinct-campus evidence."""
    campus_id_by_code = {}
    canonical_codes = []
    for campus_id, campus_code in campus_rows:
        canonical_code = canonicalize_stage6_campus_code(campus_code)
        existing_campus_id = campus_id_by_code.get(canonical_code)
        if existing_campus_id is not None and existing_campus_id != campus_id:
            raise Stage6CampusCodeAmbiguity(
                "Distinct participating campuses resolve to the same Stage 6 "
                f"campus code: {canonical_code or '(blank)'}"
            )
        if existing_campus_id is None:
            campus_id_by_code[canonical_code] = campus_id
            canonical_codes.append(canonical_code)
    return tuple(canonical_codes)
