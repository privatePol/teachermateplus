from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from functools import lru_cache
from math import lcm


CAMPUS_WEIGHTS = {"CUBAO": 33, "FAIRVIEW": 33, "TAYTAY": 34}
CAMPUS_TIE_PRIORITY = ("CUBAO", "FAIRVIEW", "TAYTAY")
DIFFICULTY_WEIGHTS = {"EASY": 30, "MODERATE": 50, "DIFFICULT": 20}
DIFFICULTY_TIE_PRIORITY = ("MODERATE", "EASY", "DIFFICULT")


class AllocationError(ValueError):
    pass


class FeasibilityLimitExceeded(RuntimeError):
    pass


SELECTION_HMAC_DOMAIN = "departmental-exams.stage6b.selection"
ORDER_A_HMAC_DOMAIN = "departmental-exams.stage6b.order.set-a"
ORDER_B_HMAC_DOMAIN = "departmental-exams.stage6b.order.set-b"


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


@dataclass(frozen=True)
class IdentityMember:
    source_id: int
    contributor_id: int
    campus: str
    difficulty: str
    section_id: int
    member_order: int = 1


@dataclass(frozen=True)
class IdentityBlock:
    block_id: str
    vector: tuple[int, ...]
    members: tuple[IdentityMember, ...]

    @property
    def size(self):
        return len(self.members)


@dataclass(frozen=True)
class IdentitySelectionResult:
    feasible: bool
    limit_hit: bool
    states_explored: int
    set_a_block_ids: tuple[str, ...] = ()
    set_b_block_ids: tuple[str, ...] = ()
    overlap: int | None = None
    proportional_score: int | None = None
    contributors_represented: int = 0
    squared_contributor_concentration: int = 0


def confidential_hmac_rank(*, secret, domain: str, context) -> int:
    """Return an internal deterministic rank without exposing secret material."""
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if not key:
        raise ValueError("A non-empty server secret is required for HMAC ranking.")
    material = json.dumps(
        context,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    digest = hmac.new(
        key,
        domain.encode("utf-8") + b"\x00" + material,
        hashlib.sha256,
    ).digest()
    return int.from_bytes(digest, "big")


def proportional_campus_difficulty_score(
    *, total, campus_quotas, difficulty_quotas, cell_counts
):
    """Calculate the exact integer-scaled proportional deviation policy."""
    positive_products = [
        campus_total * difficulty_total
        for campus_total in campus_quotas.values()
        for difficulty_total in difficulty_quotas.values()
        if campus_total * difficulty_total > 0
    ]
    if not positive_products:
        return 0
    scale = lcm(*positive_products)
    score = 0
    for campus, campus_total in campus_quotas.items():
        for difficulty, difficulty_total in difficulty_quotas.items():
            product = campus_total * difficulty_total
            if product <= 0:
                if cell_counts.get((campus, difficulty), 0):
                    raise ValueError("A zero-margin campus/difficulty cell must remain empty.")
                continue
            actual = int(cell_counts.get((campus, difficulty), 0))
            score += (total * actual - product) ** 2 * (scale // product)
    return score


def _solve_identity_aware_two_sets_ungrouped(
    *,
    margins,
    blocks,
    minimum_overlap,
    campus_quotas,
    difficulty_quotas,
    secret,
    hmac_context,
    max_states=500_000,
):
    """Prove the lexicographically optimal identity-aware A/B selection.

    The search is deliberately bounded. If the bound is reached, no incumbent
    is returned because its optimality has not been proved.
    """
    margins = tuple(int(value) for value in margins)
    minimum_overlap = int(minimum_overlap)
    if not margins or minimum_overlap < 0:
        return IdentitySelectionResult(False, False, 0)
    normalized = tuple(
        sorted(
            blocks,
            key=lambda block: (-block.size, block.vector, str(block.block_id)),
        )
    )
    dimension_count = len(margins)
    if any(len(block.vector) != dimension_count for block in normalized):
        raise ValueError("Identity block vector dimension does not match margins.")
    if any(block.vector[0] != block.size for block in normalized):
        raise ValueError("Identity block total must equal its member count.")
    source_ids = [member.source_id for block in normalized for member in block.members]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Each source question may belong to only one selection block.")

    campus_order = tuple(campus_quotas)
    difficulty_order = tuple(difficulty_quotas)
    cell_order = tuple(
        (campus, difficulty)
        for campus in campus_order
        for difficulty in difficulty_order
    )
    cell_index = {cell: index for index, cell in enumerate(cell_order)}
    contributor_order = tuple(
        sorted({member.contributor_id for block in normalized for member in block.members})
    )
    contributor_index = {
        contributor_id: index for index, contributor_id in enumerate(contributor_order)
    }

    block_cell_vectors = []
    block_contributor_vectors = []
    for block in normalized:
        cell_vector = [0] * len(cell_order)
        contributor_vector = [0] * len(contributor_order)
        for member in block.members:
            try:
                cell_vector[cell_index[(member.campus, member.difficulty)]] += 1
            except KeyError as exc:
                raise ValueError("Identity member uses a non-margin cell.") from exc
            contributor_vector[contributor_index[member.contributor_id]] += 1
        block_cell_vectors.append(tuple(cell_vector))
        block_contributor_vectors.append(tuple(contributor_vector))

    zero_vector = (0,) * dimension_count
    suffix = [zero_vector for _ in range(len(normalized) + 1)]
    for index in range(len(normalized) - 1, -1, -1):
        suffix[index] = tuple(
            suffix[index + 1][position] + normalized[index].vector[position]
            for position in range(dimension_count)
        )

    state_count = 0
    seen_hmac = {}
    best_score = None
    best_selection = None
    selected_a = []
    selected_b = []

    def add_vector(left, right, multiplier=1):
        return tuple(left[index] + multiplier * right[index] for index in range(len(left)))

    def subtract_vector(left, right):
        return tuple(left[index] - right[index] for index in range(len(left)))

    def search(
        index,
        remaining_a,
        remaining_b,
        overlap,
        cells_a,
        cells_b,
        appearances,
        hmac_total,
    ):
        nonlocal state_count, best_score, best_selection
        state_count += 1
        if state_count > max_states:
            raise FeasibilityLimitExceeded
        if overlap > minimum_overlap:
            return
        available = suffix[index]
        if any(
            value < 0 or value > available[position]
            for position, value in enumerate(remaining_a)
        ) or any(
            value < 0 or value > available[position]
            for position, value in enumerate(remaining_b)
        ):
            return
        unavoidable = max(0, remaining_a[0] + remaining_b[0] - available[0])
        if overlap + unavoidable > minimum_overlap:
            return
        state_key = (
            index,
            remaining_a,
            remaining_b,
            overlap,
            cells_a,
            cells_b,
            appearances,
        )
        previous_hmac = seen_hmac.get(state_key)
        if previous_hmac is not None and previous_hmac <= hmac_total:
            return
        seen_hmac[state_key] = hmac_total
        if index == len(normalized):
            if remaining_a != zero_vector or remaining_b != zero_vector:
                return
            if overlap != minimum_overlap:
                return
            counts_a = {cell: cells_a[position] for position, cell in enumerate(cell_order)}
            counts_b = {cell: cells_b[position] for position, cell in enumerate(cell_order)}
            proportional = proportional_campus_difficulty_score(
                total=margins[0],
                campus_quotas=campus_quotas,
                difficulty_quotas=difficulty_quotas,
                cell_counts=counts_a,
            ) + proportional_campus_difficulty_score(
                total=margins[0],
                campus_quotas=campus_quotas,
                difficulty_quotas=difficulty_quotas,
                cell_counts=counts_b,
            )
            represented = sum(1 for count in appearances if count)
            concentration = sum(count * count for count in appearances)
            canonical_a = tuple(sorted(selected_a))
            canonical_b = tuple(sorted(selected_b))
            score = (
                proportional,
                -represented,
                concentration,
                hmac_total,
                canonical_a,
                canonical_b,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_selection = (canonical_a, canonical_b, represented, concentration)
            return

        block = normalized[index]
        block_cells = block_cell_vectors[index]
        block_contributors = block_contributor_vectors[index]
        decisions = []
        for use_a, use_b, label in (
            (0, 0, "NEITHER"),
            (1, 0, "A"),
            (0, 1, "B"),
            (1, 1, "BOTH"),
        ):
            if use_a and any(
                block.vector[position] > remaining_a[position]
                for position in range(dimension_count)
            ):
                continue
            if use_b and any(
                block.vector[position] > remaining_b[position]
                for position in range(dimension_count)
            ):
                continue
            local_overlap = block.size if use_a and use_b else 0
            if overlap + local_overlap > minimum_overlap:
                continue
            rank = 0
            if use_a or use_b:
                rank = confidential_hmac_rank(
                    secret=secret,
                    domain=SELECTION_HMAC_DOMAIN,
                    context={
                        **dict(hmac_context),
                        "block_identity": block.block_id,
                        "selection_state": label,
                    },
                )
            decisions.append((local_overlap, rank, use_a, use_b))
        decisions.sort()
        for local_overlap, rank, use_a, use_b in decisions:
            if use_a:
                selected_a.append(str(block.block_id))
            if use_b:
                selected_b.append(str(block.block_id))
            next_a = subtract_vector(remaining_a, block.vector) if use_a else remaining_a
            next_b = subtract_vector(remaining_b, block.vector) if use_b else remaining_b
            search(
                index + 1,
                next_a,
                next_b,
                overlap + local_overlap,
                add_vector(cells_a, block_cells, use_a),
                add_vector(cells_b, block_cells, use_b),
                add_vector(appearances, block_contributors, use_a + use_b),
                hmac_total + rank,
            )
            if use_b:
                selected_b.pop()
            if use_a:
                selected_a.pop()

    try:
        search(
            0,
            margins,
            margins,
            0,
            (0,) * len(cell_order),
            (0,) * len(cell_order),
            (0,) * len(contributor_order),
            0,
        )
    except FeasibilityLimitExceeded:
        return IdentitySelectionResult(False, True, state_count)
    if best_selection is None:
        return IdentitySelectionResult(False, False, state_count)
    set_a, set_b, represented, concentration = best_selection
    return IdentitySelectionResult(
        feasible=True,
        limit_hit=False,
        states_explored=state_count,
        set_a_block_ids=set_a,
        set_b_block_ids=set_b,
        overlap=minimum_overlap,
        proportional_score=best_score[0],
        contributors_represented=represented,
        squared_contributor_concentration=concentration,
    )


def solve_identity_aware_two_sets(
    *,
    margins,
    blocks,
    minimum_overlap,
    campus_quotas,
    difficulty_quotas,
    secret,
    hmac_context,
    max_states=500_000,
):
    """Prove the exact Stage 6B objective with safe singleton compression.

    Singleton questions are interchangeable for higher objectives only when
    hard vector, contributor, and campus/difficulty/section cell all match.
    Their exact identities are then assigned to A/B/Both/Neither with a cached
    HMAC-optimal cardinality assignment. Scenarios remain individual atomic
    blocks.
    """
    margins = tuple(int(value) for value in margins)
    minimum_overlap = int(minimum_overlap)
    if not margins or minimum_overlap < 0:
        return IdentitySelectionResult(False, False, 0)
    normalized_blocks = tuple(
        sorted(blocks, key=lambda block: (-block.size, block.vector, str(block.block_id)))
    )
    dimension_count = len(margins)
    if any(len(block.vector) != dimension_count for block in normalized_blocks):
        raise ValueError("Identity block vector dimension does not match margins.")
    if any(block.vector[0] != block.size for block in normalized_blocks):
        raise ValueError("Identity block total must equal its member count.")
    source_ids = [
        member.source_id for block in normalized_blocks for member in block.members
    ]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Each source question may belong to only one selection block.")

    campus_order = tuple(campus_quotas)
    difficulty_order = tuple(difficulty_quotas)
    cell_order = tuple(
        (campus, difficulty)
        for campus in campus_order
        for difficulty in difficulty_order
    )
    cell_index = {cell: index for index, cell in enumerate(cell_order)}
    contributor_order = tuple(
        sorted(
            {
                member.contributor_id
                for block in normalized_blocks
                for member in block.members
            }
        )
    )
    contributor_index = {
        contributor_id: index
        for index, contributor_id in enumerate(contributor_order)
    }

    singleton_groups = {}
    atomic_groups = []
    for block in normalized_blocks:
        if block.size != 1:
            atomic_groups.append(("atomic", (block,)))
            continue
        member = block.members[0]
        key = (
            block.vector,
            member.contributor_id,
            member.campus,
            member.difficulty,
            member.section_id,
        )
        singleton_groups.setdefault(key, []).append(block)
    groups = atomic_groups + [
        ("singletons", tuple(sorted(rows, key=lambda row: str(row.block_id))))
        for _key, rows in sorted(singleton_groups.items(), key=lambda item: item[0])
    ]
    groups.sort(
        key=lambda group: (
            0 if group[0] == "atomic" else 1,
            -sum(block.size for block in group[1]),
            group[1][0].vector,
            str(group[1][0].block_id),
        )
    )

    group_vectors = []
    group_cell_vectors = []
    group_total_cell_vectors = []
    group_contributor_vectors = []
    for kind, group_blocks in groups:
        vector = tuple(
            sum(block.vector[position] for block in group_blocks)
            for position in range(dimension_count)
        )
        group_vectors.append(vector)
        cell_vector = [0] * len(cell_order)
        contributor_vector = [0] * len(contributor_order)
        if kind == "atomic":
            members = group_blocks[0].members
        else:
            members = group_blocks[0].members
        for member in members:
            try:
                cell_vector[cell_index[(member.campus, member.difficulty)]] += 1
            except KeyError as exc:
                raise ValueError("Identity member uses a non-margin cell.") from exc
            contributor_vector[contributor_index[member.contributor_id]] += 1
        group_cell_vectors.append(tuple(cell_vector))
        group_total_cell_vectors.append(
            tuple(
                value * (len(group_blocks) if kind == "singletons" else 1)
                for value in cell_vector
            )
        )
        group_contributor_vectors.append(tuple(contributor_vector))

    zero_vector = (0,) * dimension_count
    suffix = [zero_vector for _ in range(len(groups) + 1)]
    for index in range(len(groups) - 1, -1, -1):
        suffix[index] = tuple(
            suffix[index + 1][position] + group_vectors[index][position]
            for position in range(dimension_count)
        )
    zero_cells = (0,) * len(cell_order)
    suffix_cells = [zero_cells for _ in range(len(groups) + 1)]
    for index in range(len(groups) - 1, -1, -1):
        suffix_cells[index] = tuple(
            suffix_cells[index + 1][position]
            + group_total_cell_vectors[index][position]
            for position in range(len(cell_order))
        )
    cell_products = tuple(
        campus_quotas[campus] * difficulty_quotas[difficulty]
        for campus, difficulty in cell_order
    )
    proportional_scale = lcm(*(value for value in cell_products if value > 0))

    def proportional_lower_bound(index, cells_a, cells_b):
        score = 0
        future = suffix_cells[index]
        for current_cells in (cells_a, cells_b):
            for position, product in enumerate(cell_products):
                if product <= 0:
                    continue
                lower = current_cells[position]
                upper = lower + future[position]
                floor_expected = product // margins[0]
                candidates = {
                    lower,
                    upper,
                    max(lower, min(upper, floor_expected)),
                    max(lower, min(upper, floor_expected + 1)),
                }
                score += min(
                    (margins[0] * value - product) ** 2
                    * (proportional_scale // product)
                    for value in candidates
                )
        return score

    state_count = 0

    def count_state():
        nonlocal state_count
        state_count += 1
        if state_count > max_states:
            raise FeasibilityLimitExceeded

    def make_assignment_solver(rows, costs):
        @lru_cache(maxsize=None)
        def assign(need_a, need_b, need_both):
            count_state()
            if min(need_a, need_b, need_both) < 0:
                return None
            need_neither = len(rows) - need_a - need_b - need_both
            if need_neither < 0:
                return None
            slots = (
                ["N"] * need_neither
                + ["A"] * need_a
                + ["B"] * need_b
                + ["X"] * need_both
            )
            size = len(rows)
            # Hungarian minimum-cost perfect assignment. Repeated category
            # slots enforce exact cardinalities without enumerating subsets.
            u = [0] * (size + 1)
            v = [0] * (size + 1)
            p = [0] * (size + 1)
            way = [0] * (size + 1)
            for row_index in range(1, size + 1):
                p[0] = row_index
                column = 0
                min_value = [None] * (size + 1)
                used = [False] * (size + 1)
                while True:
                    used[column] = True
                    active_row = p[column]
                    delta = None
                    next_column = 0
                    for candidate_column in range(1, size + 1):
                        if used[candidate_column]:
                            continue
                        reduced = (
                            costs[active_row - 1][slots[candidate_column - 1]]
                            - u[active_row]
                            - v[candidate_column]
                        )
                        if (
                            min_value[candidate_column] is None
                            or reduced < min_value[candidate_column]
                        ):
                            min_value[candidate_column] = reduced
                            way[candidate_column] = column
                        if delta is None or min_value[candidate_column] < delta:
                            delta = min_value[candidate_column]
                            next_column = candidate_column
                    for candidate_column in range(size + 1):
                        if used[candidate_column]:
                            u[p[candidate_column]] += delta
                            v[candidate_column] -= delta
                        elif candidate_column:
                            min_value[candidate_column] -= delta
                    column = next_column
                    if p[column] == 0:
                        break
                while True:
                    previous = way[column]
                    p[column] = p[previous]
                    column = previous
                    if column == 0:
                        break
            labels = [None] * size
            for column in range(1, size + 1):
                labels[p[column] - 1] = slots[column - 1]
            total_cost = sum(
                costs[index][label] for index, label in enumerate(labels)
            )
            return total_cost, tuple(labels)

        return assign

    assignment_solvers = []
    for kind, group_blocks in groups:
        if kind == "atomic":
            assignment_solvers.append(None)
            continue
        rows = tuple(group_blocks)
        costs = tuple(
            {
                "N": 0,
                "A": confidential_hmac_rank(
                    secret=secret,
                    domain=SELECTION_HMAC_DOMAIN,
                    context={
                        **dict(hmac_context),
                        "block_identity": block.block_id,
                        "selection_state": "A",
                    },
                ),
                "B": confidential_hmac_rank(
                    secret=secret,
                    domain=SELECTION_HMAC_DOMAIN,
                    context={
                        **dict(hmac_context),
                        "block_identity": block.block_id,
                        "selection_state": "B",
                    },
                ),
                "X": confidential_hmac_rank(
                    secret=secret,
                    domain=SELECTION_HMAC_DOMAIN,
                    context={
                        **dict(hmac_context),
                        "block_identity": block.block_id,
                        "selection_state": "BOTH",
                    },
                ),
            }
            for block in rows
        )

        assignment_solvers.append(make_assignment_solver(rows, costs))

    seen_hmac = {}
    best_score = None
    best_selection = None
    selected_a = []
    selected_b = []

    def add_vector(left, right, multiplier=1):
        return tuple(
            left[position] + multiplier * right[position]
            for position in range(len(left))
        )

    def subtract_multiple(left, unit, amount):
        return tuple(
            left[position] - unit[position] * amount
            for position in range(len(left))
        )

    def search(
        index,
        remaining_a,
        remaining_b,
        overlap,
        cells_a,
        cells_b,
        appearances,
        hmac_total,
    ):
        nonlocal best_score, best_selection
        count_state()
        if overlap > minimum_overlap:
            return
        available = suffix[index]
        if any(
            value < 0 or value > available[position]
            for position, value in enumerate(remaining_a)
        ) or any(
            value < 0 or value > available[position]
            for position, value in enumerate(remaining_b)
        ):
            return
        unavoidable = max(0, remaining_a[0] + remaining_b[0] - available[0])
        if overlap + unavoidable > minimum_overlap:
            return
        if (
            best_score is not None
            and proportional_lower_bound(index, cells_a, cells_b) > best_score[0]
        ):
            return
        state_key = (
            index,
            remaining_a,
            remaining_b,
            overlap,
            cells_a,
            cells_b,
            appearances,
        )
        previous_hmac = seen_hmac.get(state_key)
        if previous_hmac is not None and previous_hmac <= hmac_total:
            return
        seen_hmac[state_key] = hmac_total
        if index == len(groups):
            if remaining_a != zero_vector or remaining_b != zero_vector:
                return
            if overlap != minimum_overlap:
                return
            counts_a = {
                cell: cells_a[position] for position, cell in enumerate(cell_order)
            }
            counts_b = {
                cell: cells_b[position] for position, cell in enumerate(cell_order)
            }
            proportional = proportional_campus_difficulty_score(
                total=margins[0],
                campus_quotas=campus_quotas,
                difficulty_quotas=difficulty_quotas,
                cell_counts=counts_a,
            ) + proportional_campus_difficulty_score(
                total=margins[0],
                campus_quotas=campus_quotas,
                difficulty_quotas=difficulty_quotas,
                cell_counts=counts_b,
            )
            represented = sum(1 for count in appearances if count)
            concentration = sum(count * count for count in appearances)
            canonical_a = tuple(sorted(selected_a))
            canonical_b = tuple(sorted(selected_b))
            score = (
                proportional,
                -represented,
                concentration,
                hmac_total,
                canonical_a,
                canonical_b,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_selection = (
                    canonical_a,
                    canonical_b,
                    represented,
                    concentration,
                )
            return

        kind, group_blocks = groups[index]
        decisions = []
        if kind == "atomic":
            block = group_blocks[0]
            block_cells = group_cell_vectors[index]
            block_contributors = group_contributor_vectors[index]
            for use_a, use_b, label in (
                (0, 0, "NEITHER"),
                (1, 0, "A"),
                (0, 1, "B"),
                (1, 1, "BOTH"),
            ):
                if use_a and any(
                    block.vector[position] > remaining_a[position]
                    for position in range(dimension_count)
                ):
                    continue
                if use_b and any(
                    block.vector[position] > remaining_b[position]
                    for position in range(dimension_count)
                ):
                    continue
                local_overlap = block.size if use_a and use_b else 0
                if overlap + local_overlap > minimum_overlap:
                    continue
                rank = 0
                if use_a or use_b:
                    rank = confidential_hmac_rank(
                        secret=secret,
                        domain=SELECTION_HMAC_DOMAIN,
                        context={
                            **dict(hmac_context),
                            "block_identity": block.block_id,
                            "selection_state": label,
                        },
                    )
                decisions.append(
                    (
                        local_overlap,
                        rank,
                        use_a,
                        use_b,
                        (str(block.block_id),) if use_a else (),
                        (str(block.block_id),) if use_b else (),
                        block.vector,
                        block_cells,
                        block_contributors,
                    )
                )
        else:
            capacity = len(group_blocks)
            unit = group_blocks[0].vector
            future = suffix[index + 1]
            affected = tuple(
                position for position, value in enumerate(unit) if value
            )
            lower_a = max(
                [0]
                + [remaining_a[position] - future[position] for position in affected]
            )
            upper_a = min(
                [capacity]
                + [remaining_a[position] // unit[position] for position in affected]
            )
            lower_b = max(
                [0]
                + [remaining_b[position] - future[position] for position in affected]
            )
            upper_b = min(
                [capacity]
                + [remaining_b[position] // unit[position] for position in affected]
            )
            for amount_a in range(lower_a, upper_a + 1):
                for amount_b in range(lower_b, upper_b + 1):
                    lower_both = max(0, amount_a + amount_b - capacity)
                    upper_both = min(
                        amount_a,
                        amount_b,
                        minimum_overlap - overlap,
                    )
                    for amount_both in range(lower_both, upper_both + 1):
                        assignment = assignment_solvers[index](
                            amount_a - amount_both,
                            amount_b - amount_both,
                            amount_both,
                        )
                        if assignment is None:
                            continue
                        labels = assignment[1]
                        chosen_a = tuple(
                            str(block.block_id)
                            for block, label in zip(group_blocks, labels)
                            if label in ("A", "X")
                        )
                        chosen_b = tuple(
                            str(block.block_id)
                            for block, label in zip(group_blocks, labels)
                            if label in ("B", "X")
                        )
                        decisions.append(
                            (
                                amount_both,
                                assignment[0],
                                amount_a,
                                amount_b,
                                chosen_a,
                                chosen_b,
                                unit,
                                group_cell_vectors[index],
                                group_contributor_vectors[index],
                            )
                        )
        decisions.sort(
            key=lambda row: (
                proportional_lower_bound(
                    index + 1,
                    add_vector(cells_a, row[7], row[2]),
                    add_vector(cells_b, row[7], row[3]),
                ),
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
            )
        )
        for (
            local_overlap,
            rank,
            amount_a,
            amount_b,
            chosen_a,
            chosen_b,
            unit_vector,
            unit_cells,
            unit_contributors,
        ) in decisions:
            selected_a.extend(chosen_a)
            selected_b.extend(chosen_b)
            search(
                index + 1,
                subtract_multiple(remaining_a, unit_vector, amount_a),
                subtract_multiple(remaining_b, unit_vector, amount_b),
                overlap + local_overlap,
                add_vector(cells_a, unit_cells, amount_a),
                add_vector(cells_b, unit_cells, amount_b),
                add_vector(
                    appearances,
                    unit_contributors,
                    amount_a + amount_b,
                ),
                hmac_total + rank,
            )
            del selected_a[len(selected_a) - len(chosen_a) :]
            del selected_b[len(selected_b) - len(chosen_b) :]

    try:
        search(
            0,
            margins,
            margins,
            0,
            (0,) * len(cell_order),
            (0,) * len(cell_order),
            (0,) * len(contributor_order),
            0,
        )
    except FeasibilityLimitExceeded:
        return IdentitySelectionResult(False, True, state_count)
    if best_selection is None:
        return IdentitySelectionResult(False, False, state_count)
    set_a, set_b, represented, concentration = best_selection
    return IdentitySelectionResult(
        feasible=True,
        limit_hit=False,
        states_explored=state_count,
        set_a_block_ids=set_a,
        set_b_block_ids=set_b,
        overlap=minimum_overlap,
        proportional_score=best_score[0],
        contributors_represented=represented,
        squared_contributor_concentration=concentration,
    )


def order_selected_blocks(
    *, blocks, selected_block_ids, set_code, secret, hmac_context, section_order=None
):
    """Order blocks confidentially while retaining scenario member contiguity."""
    domain = ORDER_A_HMAC_DOMAIN if set_code == "A" else ORDER_B_HMAC_DOMAIN
    by_id = {str(block.block_id): block for block in blocks}
    selected = [by_id[str(block_id)] for block_id in selected_block_ids]
    section_priority = {
        section_id: index
        for index, section_id in enumerate(section_order or (), start=1)
    }
    selected.sort(
        key=lambda block: (
            section_priority.get(
                block.members[0].section_id,
                block.members[0].section_id,
            ),
            confidential_hmac_rank(
                secret=secret,
                domain=domain,
                context={
                    **dict(hmac_context),
                    "set_identity": set_code,
                    "section_identity": block.members[0].section_id,
                    "block_identity": block.block_id,
                },
            ),
            str(block.block_id),
        )
    )
    ordered = []
    for block in selected:
            ordered.extend(sorted(block.members, key=lambda member: member.member_order))
    return tuple(ordered)


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
