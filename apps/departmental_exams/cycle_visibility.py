"""List filtering only. Historical object lookups retain their own authority."""
def selected_cycle_status(params, *, allow_draft=True):
    allowed = ("OPEN", "CLOSED", "DRAFT") if allow_draft else ("OPEN", "CLOSED")
    value = params.get("cycle_status", "OPEN")
    return value if value in allowed else "OPEN"


def filter_cycle_rows(rows, params, *, cycle_of=lambda row: row.cycle, allow_draft=True):
    status = selected_cycle_status(params, allow_draft=allow_draft)
    return [row for row in rows if cycle_of(row).status == status]
