from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


CAMPUS_WEIGHTS = {"CUBAO": 33, "FAIRVIEW": 33, "TAYTAY": 34}
CAMPUS_TIE_PRIORITY = ("CUBAO", "FAIRVIEW", "TAYTAY")
DIFFICULTY_WEIGHTS = {"EASY": 30, "MODERATE": 50, "DIFFICULT": 20}
DIFFICULTY_TIE_PRIORITY = ("MODERATE", "EASY", "DIFFICULT")


class AllocationError(ValueError):
    pass


class FeasibilityLimitExceeded(RuntimeError):
    pass


def hamilton_allocate(*, total: int, weights: dict[str, int], tie_priority) -> dict[str, int]:
    """Allocate an integer total with exact largest-remainder arithmetic."""
    if not isinstance(total, int) or total < 0:
        raise AllocationError("Allocation total must be a nonnegative integer.")
    if not weights or any(not isinstance(value, int) or value <= 0 for value in weights.values()):
        raise AllocationError("Allocation weights must be positive integers.")
    priority = {key: index for index, key in enumerate(tie_priority)}
    if set(priority) != set(weights):
        raise AllocationError("Tie priority must contain every weighted key exactly once.")
    denominator = sum(weights.values())
    result = {}
    remainders = []
    assigned = 0
    for key, weight in weights.items():
        quotient, remainder = divmod(total * weight, denominator)
        result[key] = quotient
        assigned += quotient
        remainders.append((remainder, priority[key], key))
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for _remainder, _priority, key in remainders[: total - assigned]:
        result[key] += 1
    return result


def allocate_campuses(total: int, campus_codes) -> dict[str, int]:
    codes = tuple(dict.fromkeys((code or "").strip().upper() for code in campus_codes))
    if not codes:
        raise AllocationError("At least one participating campus is required.")
    unknown = sorted(set(codes) - set(CAMPUS_WEIGHTS))
    if unknown:
        raise AllocationError("Unknown participating campus code: " + ", ".join(unknown))
    ordered = tuple(code for code in CAMPUS_TIE_PRIORITY if code in codes)
    weights = {code: CAMPUS_WEIGHTS[code] for code in ordered}
    return hamilton_allocate(total=total, weights=weights, tie_priority=ordered)


def allocate_difficulties(total: int) -> dict[str, int]:
    return hamilton_allocate(
        total=total,
        weights=DIFFICULTY_WEIGHTS,
        tie_priority=DIFFICULTY_TIE_PRIORITY,
    )


@dataclass(frozen=True)
class FeasibilityResult:
    feasible: bool
    minimum_overlap: int | None
    states_explored: int
    limit_hit: bool = False


def solve_two_set_feasibility(
    *,
    margins: tuple[int, ...],
    scenario_vectors,
    singleton_capacities,
    max_states: int = 250_000,
) -> FeasibilityResult:
    """Find the exact minimum overlap for two equivalent constrained sets.

    Scenario vectors are indivisible. Singleton capacities are grouped by their
    identical campus/difficulty/section vector, so no source identifier is
    needed or returned by the solver.
    """
    margins = tuple(int(value) for value in margins)
    dimension_count = len(margins)
    if not margins or any(value < 0 for value in margins):
        return FeasibilityResult(False, None, 0)
    scenarios = tuple(
        sorted(
            (tuple(int(value) for value in vector) for vector in scenario_vectors),
            key=lambda vector: (-vector[0], vector),
        )
    )
    cells = tuple(
        sorted(
            (
                (tuple(int(value) for value in vector), int(capacity))
                for vector, capacity in singleton_capacities.items()
                if int(capacity) > 0
            ),
            key=lambda item: (item[0], item[1]),
        )
    )
    if any(len(vector) != dimension_count for vector in scenarios):
        raise ValueError("Scenario vector dimension does not match margins.")
    if any(len(vector) != dimension_count for vector, _capacity in cells):
        raise ValueError("Singleton vector dimension does not match margins.")
    if any(any(value < 0 for value in vector) for vector in scenarios):
        raise ValueError("Scenario vectors cannot contain negative values.")
    if any(
        any(value not in (0, 1) for value in vector) or vector[0] != 1
        for vector, _capacity in cells
    ):
        raise ValueError("Singleton vectors must be zero/one vectors with total one.")

    state_counter = 0

    def count_state():
        nonlocal state_counter
        state_counter += 1
        if state_counter > max_states:
            raise FeasibilityLimitExceeded

    zero = (0,) * dimension_count

    cell_suffix = [zero for _ in range(len(cells) + 1)]
    for index in range(len(cells) - 1, -1, -1):
        vector, capacity = cells[index]
        cell_suffix[index] = tuple(
            cell_suffix[index + 1][position] + vector[position] * capacity
            for position in range(dimension_count)
        )

    @lru_cache(maxsize=None)
    def solve_singletons(index, remaining_a, remaining_b):
        count_state()
        if remaining_b < remaining_a:
            return solve_singletons(index, remaining_b, remaining_a)
        available = cell_suffix[index]
        if any(
            remaining_a[position] > available[position]
            or remaining_b[position] > available[position]
            for position in range(dimension_count)
        ):
            return None
        if index == len(cells):
            return 0 if remaining_a == zero and remaining_b == zero else None
        vector, capacity = cells[index]
        future = cell_suffix[index + 1]
        affected = tuple(position for position, value in enumerate(vector) if value)
        if any(
            not vector[position]
            and (
                remaining_a[position] > future[position]
                or remaining_b[position] > future[position]
            )
            for position in range(dimension_count)
        ):
            return None
        lower_a = max(
            [0]
            + [remaining_a[position] - future[position] for position in affected]
        )
        upper_a = min([capacity] + [remaining_a[position] for position in affected])
        lower_b = max(
            [0]
            + [remaining_b[position] - future[position] for position in affected]
        )
        upper_b = min([capacity] + [remaining_b[position] for position in affected])
        if lower_a > upper_a or lower_b > upper_b:
            return None
        best = None
        amount_pairs = [
            (amount_a, amount_b)
            for amount_a in range(lower_a, upper_a + 1)
            for amount_b in range(lower_b, upper_b + 1)
        ]
        amount_pairs.sort(
            key=lambda pair: (
                max(0, pair[0] + pair[1] - capacity),
                sum(
                    abs(pair[0] * available[position] - remaining_a[position] * capacity)
                    + abs(pair[1] * available[position] - remaining_b[position] * capacity)
                    for position in affected
                ),
                pair,
            )
        )
        for amount_a, amount_b in amount_pairs:
            next_a = tuple(
                remaining_a[position] - vector[position] * amount_a
                for position in range(dimension_count)
            )
            local_overlap = max(0, amount_a + amount_b - capacity)
            if best is not None and local_overlap >= best:
                continue
            next_b = tuple(
                remaining_b[position] - vector[position] * amount_b
                for position in range(dimension_count)
            )
            if next_b < next_a:
                next_a, next_b = next_b, next_a
            tail = solve_singletons(index + 1, next_a, next_b)
            if tail is None:
                continue
            candidate = local_overlap + tail
            if best is None or candidate < best:
                best = candidate
                if best == 0:
                    break
            if best == 0:
                break
        return best

    scenario_suffix = [zero for _ in range(len(scenarios) + 1)]
    for index in range(len(scenarios) - 1, -1, -1):
        scenario_suffix[index] = tuple(
            scenario_suffix[index + 1][position] + scenarios[index][position]
            for position in range(dimension_count)
        )

    @lru_cache(maxsize=None)
    def solve_scenarios(index, remaining_a, remaining_b):
        count_state()
        if remaining_b < remaining_a:
            return solve_scenarios(index, remaining_b, remaining_a)
        available = tuple(
            scenario_suffix[index][position] + cell_suffix[0][position]
            for position in range(dimension_count)
        )
        if any(
            remaining_a[position] > available[position]
            or remaining_b[position] > available[position]
            for position in range(dimension_count)
        ):
            return None
        if index == len(scenarios):
            return solve_singletons(0, remaining_a, remaining_b)
        vector = scenarios[index]
        size = vector[0]
        best = None
        # Prefer no reuse, then one-set use, and consider shared reuse last.
        for use_a, use_b in ((0, 0), (1, 0), (0, 1), (1, 1)):
            if use_a and any(vector[pos] > remaining_a[pos] for pos in range(dimension_count)):
                continue
            if use_b and any(vector[pos] > remaining_b[pos] for pos in range(dimension_count)):
                continue
            local_overlap = size if use_a and use_b else 0
            if best is not None and local_overlap >= best:
                continue
            next_a = tuple(
                remaining_a[pos] - use_a * vector[pos]
                for pos in range(dimension_count)
            )
            next_b = tuple(
                remaining_b[pos] - use_b * vector[pos]
                for pos in range(dimension_count)
            )
            if next_b < next_a:
                next_a, next_b = next_b, next_a
            tail = solve_scenarios(index + 1, next_a, next_b)
            if tail is None:
                continue
            candidate = local_overlap + tail
            if best is None or candidate < best:
                best = candidate
                if best == 0:
                    break
        return best

    try:
        minimum_overlap = solve_scenarios(0, margins, margins)
    except FeasibilityLimitExceeded:
        return FeasibilityResult(False, None, state_counter, limit_hit=True)
    return FeasibilityResult(
        feasible=minimum_overlap is not None,
        minimum_overlap=minimum_overlap,
        states_explored=state_counter,
    )
