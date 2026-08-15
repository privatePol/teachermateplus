from __future__ import annotations

import hashlib
import heapq
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
    logical_group_id: str | None = None

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


def _solve_logical_assignment(
    *,
    margins,
    blocks,
    logical_groups,
    minimum_overlap,
    campus_quotas,
    difficulty_quotas,
    secret,
    hmac_context,
    max_states,
    allow_soft_contributor_fallback=False,
):
    """Assignment fast path for Automatic logical singleton groups."""
    campus_order = tuple(campus_quotas)
    difficulty_order = tuple(difficulty_quotas)
    cell_order = tuple(
        (campus, difficulty)
        for campus in campus_order
        for difficulty in difficulty_order
    )
    campus_contributors = {campus: set() for campus in campus_order}
    for block in blocks:
        member = block.members[0]
        if member.campus not in campus_contributors:
            return None
        campus_contributors[member.campus].add(member.contributor_id)
    if (
        not allow_soft_contributor_fallback
        and any(
            len(contributors) != 1
            for contributors in campus_contributors.values()
        )
    ):
        return None
    if len(logical_groups) < 2 * margins[0] - minimum_overlap:
        return None

    def allocations(total, limits, position=0, prefix=()):
        if position == len(limits) - 1:
            if total <= limits[position]:
                yield prefix + (total,)
            return
        for amount in range(min(total, limits[position]) + 1):
            yield from allocations(
                total - amount,
                limits,
                position + 1,
                prefix + (amount,),
            )

    tables = []

    def build_tables(campus_index, remaining_difficulties, cells):
        if campus_index == len(campus_order):
            if not any(remaining_difficulties):
                table = dict(cells)
                tables.append(
                    (
                        proportional_campus_difficulty_score(
                            total=margins[0],
                            campus_quotas=campus_quotas,
                            difficulty_quotas=difficulty_quotas,
                            cell_counts=table,
                        ),
                        table,
                    )
                )
            return
        campus = campus_order[campus_index]
        for row in allocations(campus_quotas[campus], remaining_difficulties):
            next_remaining = tuple(
                remaining_difficulties[position] - row[position]
                for position in range(len(difficulty_order))
            )
            next_cells = dict(cells)
            for position, difficulty in enumerate(difficulty_order):
                next_cells[(campus, difficulty)] = row[position]
            build_tables(campus_index + 1, next_remaining, next_cells)

    build_tables(
        0,
        tuple(difficulty_quotas[key] for key in difficulty_order),
        {},
    )
    if not tables:
        return None
    ordered_tables = sorted(
        tables,
        key=lambda item: (
            item[0],
            tuple(item[1][cell] for cell in cell_order),
        ),
    )
    ordered_groups = tuple(
        tuple(sorted(rows, key=lambda block: str(block.block_id)))
        for _logical_id, rows in sorted(logical_groups.items())
    )
    choice_cache = {}

    def choice_for(group_index, set_code, cell):
        key = (group_index, set_code, cell)
        if key not in choice_cache:
            selection_state = {
                "A": "A",
                "B": "B",
                "X": "BOTH",
            }[set_code]
            candidates = []
            for block in ordered_groups[group_index]:
                member = block.members[0]
                if (member.campus, member.difficulty) == cell:
                    candidates.append(
                        (
                            confidential_hmac_rank(
                                secret=secret,
                                domain=SELECTION_HMAC_DOMAIN,
                                context={
                                    **dict(hmac_context),
                                    "block_identity": block.block_id,
                                    "selection_state": selection_state,
                                },
                            ),
                            str(block.block_id),
                        )
                    )
            choice_cache[key] = min(candidates) if candidates else None
        return choice_cache[key]

    def assign(slots):
        row_count = len(slots)
        column_count = len(ordered_groups)
        infinity = (1 << 256) * (row_count + 1)
        costs = []
        block_ids = []
        for set_code, cell in slots:
            row_choices = [
                choice_for(group_index, set_code, cell)
                for group_index in range(column_count)
            ]
            if not any(choice is not None for choice in row_choices):
                return None
            costs.append(
                [choice[0] if choice is not None else infinity for choice in row_choices]
            )
            block_ids.append(
                [choice[1] if choice is not None else None for choice in row_choices]
            )
        u = [0] * (row_count + 1)
        v = [0] * (column_count + 1)
        p = [0] * (column_count + 1)
        way = [0] * (column_count + 1)
        for row_index in range(1, row_count + 1):
            p[0] = row_index
            column = 0
            min_value = [None] * (column_count + 1)
            used = [False] * (column_count + 1)
            while True:
                used[column] = True
                active_row = p[column]
                delta = None
                next_column = 0
                for candidate_column in range(1, column_count + 1):
                    if used[candidate_column]:
                        continue
                    reduced = (
                        costs[active_row - 1][candidate_column - 1]
                        - u[active_row]
                        - v[candidate_column]
                    )
                    if min_value[candidate_column] is None or reduced < min_value[candidate_column]:
                        min_value[candidate_column] = reduced
                        way[candidate_column] = column
                    if delta is None or min_value[candidate_column] < delta:
                        delta = min_value[candidate_column]
                        next_column = candidate_column
                if delta is None or delta >= infinity:
                    return None
                for candidate_column in range(column_count + 1):
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
        assigned = [None] * row_count
        for column in range(1, column_count + 1):
            if p[column]:
                assigned[p[column] - 1] = column - 1
        if any(column is None for column in assigned):
            return None
        selected_a = []
        selected_b = []
        total_cost = 0
        for row_index, group_index in enumerate(assigned):
            if costs[row_index][group_index] >= infinity:
                return None
            total_cost += costs[row_index][group_index]
            set_code = slots[row_index][0]
            block_id = block_ids[row_index][group_index]
            if set_code in ("A", "X"):
                selected_a.append(block_id)
            if set_code in ("B", "X"):
                selected_b.append(block_id)
        return total_cost, tuple(sorted(selected_a)), tuple(sorted(selected_b))

    states_explored = 0
    pair_heap = [
        (score_a + ordered_tables[0][0], index_a, 0)
        for index_a, (score_a, _table_a) in enumerate(ordered_tables)
    ]
    heapq.heapify(pair_heap)
    best_score = None
    best_candidate = None

    def selection_result(*, set_a, set_b, proportional_score):
        by_id = {str(block.block_id): block for block in blocks}
        contributor_counts = {}
        for block_id in set_a + set_b:
            contributor_id = by_id[block_id].members[0].contributor_id
            contributor_counts[contributor_id] = (
                contributor_counts.get(contributor_id, 0) + 1
            )
        return IdentitySelectionResult(
            feasible=True,
            limit_hit=False,
            states_explored=states_explored,
            set_a_block_ids=set_a,
            set_b_block_ids=set_b,
            overlap=minimum_overlap,
            proportional_score=proportional_score,
            contributors_represented=len(contributor_counts),
            squared_contributor_concentration=sum(
                count * count for count in contributor_counts.values()
            ),
        )

    if minimum_overlap > 0:
        if not allow_soft_contributor_fallback:
            return None
        available_capacity = {cell: 0 for cell in cell_order}
        for rows in ordered_groups:
            for cell in {
                (block.members[0].campus, block.members[0].difficulty)
                for block in rows
            }:
                available_capacity[cell] += 1
        feasible_tables = tuple(
            (score, table)
            for score, table in ordered_tables
            if all(
                table[cell] <= available_capacity[cell]
                for cell in cell_order
            )
        )
        if not feasible_tables:
            return None

        def shared_tables(table_a, table_b):
            lower = tuple(
                max(
                    0,
                    table_a[cell]
                    + table_b[cell]
                    - available_capacity[cell],
                )
                for cell in cell_order
            )
            upper = tuple(
                min(table_a[cell], table_b[cell]) for cell in cell_order
            )
            if sum(lower) > minimum_overlap or sum(upper) < minimum_overlap:
                return
            suffix_lower = [0] * (len(cell_order) + 1)
            suffix_upper = [0] * (len(cell_order) + 1)
            for position in range(len(cell_order) - 1, -1, -1):
                suffix_lower[position] = suffix_lower[position + 1] + lower[position]
                suffix_upper[position] = suffix_upper[position + 1] + upper[position]

            def build(position, remaining, prefix):
                if position == len(cell_order):
                    if remaining == 0:
                        yield prefix
                    return
                start = max(
                    lower[position],
                    remaining - suffix_upper[position + 1],
                )
                stop = min(
                    upper[position],
                    remaining - suffix_lower[position + 1],
                )
                for amount in range(start, stop + 1):
                    yield from build(
                        position + 1,
                        remaining - amount,
                        prefix + (amount,),
                    )

            yield from build(0, minimum_overlap, ())

        pair_heap = [
            (score_a + feasible_tables[0][0], index_a, 0)
            for index_a, (score_a, _table_a) in enumerate(feasible_tables)
        ]
        heapq.heapify(pair_heap)
        while pair_heap:
            proportional_score, index_a, index_b = heapq.heappop(pair_heap)
            states_explored += 1
            if states_explored > max_states:
                return IdentitySelectionResult(False, True, states_explored)
            table_a = feasible_tables[index_a][1]
            table_b = feasible_tables[index_b][1]
            for shared in shared_tables(table_a, table_b):
                states_explored += 1
                if states_explored > max_states:
                    return IdentitySelectionResult(False, True, states_explored)
                slots = []
                for position, cell in enumerate(cell_order):
                    slots.extend(("X", cell) for _ in range(shared[position]))
                    slots.extend(
                        ("A", cell)
                        for _ in range(table_a[cell] - shared[position])
                    )
                    slots.extend(
                        ("B", cell)
                        for _ in range(table_b[cell] - shared[position])
                    )
                candidate = assign(tuple(slots))
                if candidate is not None:
                    _cost, set_a, set_b = candidate
                    return selection_result(
                        set_a=set_a,
                        set_b=set_b,
                        proportional_score=proportional_score,
                    )
            next_b = index_b + 1
            if next_b < len(feasible_tables):
                heapq.heappush(
                    pair_heap,
                    (
                        feasible_tables[index_a][0]
                        + feasible_tables[next_b][0],
                        index_a,
                        next_b,
                    ),
                )
        return None

    if allow_soft_contributor_fallback:
        fixed_capacity = {cell: 0 for cell in cell_order}
        for rows in ordered_groups:
            cells = {
                (block.members[0].campus, block.members[0].difficulty)
                for block in rows
            }
            if len(cells) == 1:
                fixed_capacity[next(iter(cells))] += 1

        def capped_table(campus_index, remaining_difficulties, capacities, cells):
            if campus_index == len(campus_order):
                return dict(cells) if not any(remaining_difficulties) else None
            campus = campus_order[campus_index]
            limits = tuple(
                min(
                    remaining_difficulties[position],
                    capacities[(campus, difficulty)],
                )
                for position, difficulty in enumerate(difficulty_order)
            )
            for row in allocations(campus_quotas[campus], limits):
                next_remaining = tuple(
                    remaining_difficulties[position] - row[position]
                    for position in range(len(difficulty_order))
                )
                next_capacities = dict(capacities)
                next_cells = dict(cells)
                for position, difficulty in enumerate(difficulty_order):
                    cell = (campus, difficulty)
                    next_capacities[cell] -= row[position]
                    next_cells[cell] = row[position]
                result = capped_table(
                    campus_index + 1,
                    next_remaining,
                    next_capacities,
                    next_cells,
                )
                if result is not None:
                    return result
            return None

        for score_a, table_a in ordered_tables:
            states_explored += 1
            if states_explored > max_states:
                return IdentitySelectionResult(False, True, states_explored)
            if any(
                table_a[cell] > fixed_capacity[cell]
                for cell in cell_order
            ):
                continue
            residual_capacity = {
                cell: fixed_capacity[cell] - table_a[cell]
                for cell in cell_order
            }
            table_b = capped_table(
                0,
                tuple(difficulty_quotas[key] for key in difficulty_order),
                residual_capacity,
                {},
            )
            if table_b is None:
                continue
            slots = []
            for set_code, table in (("A", table_a), ("B", table_b)):
                for cell in cell_order:
                    slots.extend((set_code, cell) for _ in range(table[cell]))
            candidate = assign(tuple(slots))
            if candidate is not None:
                _cost, set_a, set_b = candidate
                score_b = proportional_campus_difficulty_score(
                    total=margins[0],
                    campus_quotas=campus_quotas,
                    difficulty_quotas=difficulty_quotas,
                    cell_counts=table_b,
                )
                return selection_result(
                    set_a=set_a,
                    set_b=set_b,
                    proportional_score=score_a + score_b,
                )

    while pair_heap:
        proportional_score, index_a, index_b = heapq.heappop(pair_heap)
        if best_score is not None and proportional_score > best_score:
            break
        states_explored += 1
        if states_explored > max_states:
            return IdentitySelectionResult(False, True, states_explored)
        table_a = ordered_tables[index_a][1]
        table_b = ordered_tables[index_b][1]
        slots = []
        for set_code, table in (("A", table_a), ("B", table_b)):
            for cell in cell_order:
                slots.extend((set_code, cell) for _ in range(table[cell]))
        candidate = assign(tuple(slots))
        if candidate is not None:
            cost, set_a, set_b = candidate
            if allow_soft_contributor_fallback:
                return selection_result(
                    set_a=set_a,
                    set_b=set_b,
                    proportional_score=proportional_score,
                )
            scored_candidate = (cost, set_a, set_b)
            if best_candidate is None or scored_candidate < best_candidate:
                best_score = proportional_score
                best_candidate = scored_candidate
        next_b = index_b + 1
        if next_b < len(ordered_tables):
            heapq.heappush(
                pair_heap,
                (
                    ordered_tables[index_a][0] + ordered_tables[next_b][0],
                    index_a,
                    next_b,
                ),
            )
    if best_candidate is not None:
        _cost, set_a, set_b = best_candidate
        return selection_result(
            set_a=set_a,
            set_b=set_b,
            proportional_score=best_score,
        )
    return None


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
    stop_at_first_feasible=False,
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
    logical_groups = {}
    for block in normalized_blocks:
        if block.logical_group_id is not None:
            if block.size != 1:
                raise ValueError(
                    "A logical alternative selection block must contain one member."
                )
            logical_groups.setdefault(str(block.logical_group_id), []).append(block)
            continue
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
    if logical_groups:
        fast_result = _solve_logical_assignment(
            margins=margins,
            blocks=normalized_blocks,
            logical_groups=logical_groups,
            minimum_overlap=minimum_overlap,
            campus_quotas=campus_quotas,
            difficulty_quotas=difficulty_quotas,
            secret=secret,
            hmac_context=hmac_context,
            max_states=max_states,
            allow_soft_contributor_fallback=stop_at_first_feasible,
        )
        if fast_result is not None:
            return fast_result
    alternative_types = {}
    homogeneous_logical_types = {}
    for logical_group_id, rows in sorted(logical_groups.items()):
        ordered_rows = tuple(sorted(rows, key=lambda row: str(row.block_id)))
        if len(ordered_rows) == 1:
            block = ordered_rows[0]
            member = block.members[0]
            key = (
                block.vector,
                member.contributor_id,
                member.campus,
                member.difficulty,
                member.section_id,
            )
            singleton_groups.setdefault(key, []).append(block)
        else:
            signature = tuple(
                sorted(
                    {
                        (
                            block.vector,
                            block.members[0].contributor_id,
                            block.members[0].campus,
                            block.members[0].difficulty,
                            block.members[0].section_id,
                        )
                        for block in ordered_rows
                    }
                )
            )
            if len(signature) == 1:
                homogeneous_logical_types.setdefault(signature[0], []).append(
                    ordered_rows
                )
            else:
                alternative_types.setdefault(signature, []).append(ordered_rows)
    alternative_groups = [
        (
            "alternative_type",
            tuple(block for rows in logical_units for block in rows),
            tuple(logical_units),
        )
        for _signature, logical_units in sorted(alternative_types.items())
    ]
    homogeneous_logical_groups = [
        (
            "logical_singletons",
            tuple(block for rows in logical_units for block in rows),
            tuple(logical_units),
        )
        for _signature, logical_units in sorted(homogeneous_logical_types.items())
    ]
    groups = atomic_groups + alternative_groups + homogeneous_logical_groups + [
        ("singletons", tuple(sorted(rows, key=lambda row: str(row.block_id))))
        for _key, rows in sorted(singleton_groups.items(), key=lambda item: item[0])
    ]
    groups.sort(
        key=lambda group: (
            {
                "atomic": 0,
                "alternative_type": 1,
                "logical_singletons": 2,
                "singletons": 3,
            }[group[0]],
            len(group[2]) if group[0] == "alternative_type" else 0,
            -sum(block.size for block in group[1]),
            group[1][0].vector,
            str(group[1][0].block_id),
        )
    )

    group_vectors = []
    group_cell_vectors = []
    group_total_cell_vectors = []
    group_contributor_vectors = []
    group_total_contributor_vectors = []
    for group in groups:
        kind, group_blocks = group[:2]
        if kind == "alternative_type":
            logical_units = group[2]
            vector = tuple(
                sum(
                    max(block.vector[position] for block in rows)
                    for rows in logical_units
                )
                for position in range(dimension_count)
            )
        elif kind == "logical_singletons":
            capacity = len(group[2])
            vector = tuple(
                group_blocks[0].vector[position] * capacity
                for position in range(dimension_count)
            )
        else:
            vector = tuple(
                sum(block.vector[position] for block in group_blocks)
                for position in range(dimension_count)
            )
        group_vectors.append(vector)
        cell_vector = [0] * len(cell_order)
        contributor_vector = [0] * len(contributor_order)
        members = group_blocks[0].members
        for member in members:
            try:
                cell_vector[cell_index[(member.campus, member.difficulty)]] += 1
            except KeyError as exc:
                raise ValueError("Identity member uses a non-margin cell.") from exc
            contributor_vector[contributor_index[member.contributor_id]] += 1
        group_cell_vectors.append(tuple(cell_vector))
        if kind == "alternative_type":
            possible_cells = [0] * len(cell_order)
            for rows in group[2]:
                unit_cells = set()
                for block in rows:
                    member = block.members[0]
                    unit_cells.add(cell_index[(member.campus, member.difficulty)])
                for position in unit_cells:
                    possible_cells[position] += 1
            group_total_cell_vectors.append(tuple(possible_cells))
        else:
            capacity = (
                len(group[2])
                if kind == "logical_singletons"
                else len(group_blocks)
            )
            group_total_cell_vectors.append(
                tuple(
                    value
                    * (
                        capacity
                        if kind in ("singletons", "logical_singletons")
                        else 1
                    )
                    for value in cell_vector
                )
            )
        group_contributor_vectors.append(tuple(contributor_vector))
        if kind == "alternative_type":
            possible_contributors = [0] * len(contributor_order)
            for rows in group[2]:
                for contributor_id in {
                    block.members[0].contributor_id for block in rows
                }:
                    possible_contributors[contributor_index[contributor_id]] += 2
            group_total_contributor_vectors.append(tuple(possible_contributors))
        else:
            capacity = (
                len(group[2])
                if kind == "logical_singletons"
                else len(group_blocks)
                if kind == "singletons"
                else 1
            )
            group_total_contributor_vectors.append(
                tuple(value * capacity * 2 for value in contributor_vector)
            )

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
    zero_contributors = (0,) * len(contributor_order)
    suffix_contributors = [zero_contributors for _ in range(len(groups) + 1)]
    for index in range(len(groups) - 1, -1, -1):
        suffix_contributors[index] = tuple(
            suffix_contributors[index + 1][position]
            + group_total_contributor_vectors[index][position]
            for position in range(len(contributor_order))
        )
    cell_products = tuple(
        campus_quotas[campus] * difficulty_quotas[difficulty]
        for campus, difficulty in cell_order
    )
    proportional_scale = lcm(*(value for value in cell_products if value > 0))

    automatic_primary_bound = None
    if logical_groups:
        product_by_cell = {
            (campus, difficulty): campus_quotas[campus]
            * difficulty_quotas[difficulty]
            for campus, difficulty in cell_order
        }

        def row_allocations(total, limits, position=0, prefix=()):
            if position == len(limits) - 1:
                if total <= limits[position]:
                    yield prefix + (total,)
                return
            for amount in range(min(total, limits[position]) + 1):
                yield from row_allocations(
                    total - amount,
                    limits,
                    position + 1,
                    prefix + (amount,),
                )

        @lru_cache(maxsize=None)
        def minimum_proportional_for_rows(campus_index, remaining_difficulties):
            if campus_index == len(campus_order):
                return 0 if not any(remaining_difficulties) else None
            campus = campus_order[campus_index]
            best = None
            for allocation in row_allocations(
                campus_quotas[campus], remaining_difficulties
            ):
                tail_remaining = tuple(
                    remaining_difficulties[position] - allocation[position]
                    for position in range(len(difficulty_order))
                )
                tail = minimum_proportional_for_rows(
                    campus_index + 1, tail_remaining
                )
                if tail is None:
                    continue
                row_score = 0
                for position, difficulty in enumerate(difficulty_order):
                    product = product_by_cell[(campus, difficulty)]
                    if product > 0:
                        row_score += (
                            margins[0] * allocation[position] - product
                        ) ** 2 * (proportional_scale // product)
                candidate = row_score + tail
                if best is None or candidate < best:
                    best = candidate
            return best

        minimum_per_set_proportional = minimum_proportional_for_rows(
            0,
            tuple(difficulty_quotas[key] for key in difficulty_order),
        )

        def balanced_square(total, buckets):
            if buckets <= 0:
                return 0 if total == 0 else None
            base, remainder = divmod(total, buckets)
            return remainder * (base + 1) ** 2 + (buckets - remainder) * base**2

        contributor_campuses = {
            contributor_id: {
                member.campus
                for block in normalized_blocks
                for member in block.members
                if member.contributor_id == contributor_id
            }
            for contributor_id in contributor_order
        }
        if contributor_order and all(
            len(campuses) == 1 for campuses in contributor_campuses.values()
        ):
            minimum_concentration = 0
            for campus, quota in campus_quotas.items():
                campus_contributors = sum(
                    campuses == {campus}
                    for campuses in contributor_campuses.values()
                )
                campus_bound = balanced_square(2 * quota, campus_contributors)
                if campus_bound is None:
                    minimum_concentration = None
                    break
                minimum_concentration += campus_bound
        else:
            minimum_concentration = balanced_square(
                2 * margins[0], len(contributor_order)
            )
        if (
            minimum_per_set_proportional is not None
            and minimum_concentration is not None
        ):
            automatic_primary_bound = (
                2 * minimum_per_set_proportional,
                -len(contributor_order),
                minimum_concentration,
            )

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
    assignment_block_ids = []
    for group in groups:
        kind, group_blocks = group[:2]
        if kind not in ("singletons", "logical_singletons"):
            assignment_solvers.append(None)
            assignment_block_ids.append(None)
            continue
        logical_units = (
            group[2]
            if kind == "logical_singletons"
            else tuple((block,) for block in group_blocks)
        )
        rows = tuple(unit[0] for unit in logical_units)
        costs = []
        block_ids = []
        for unit in logical_units:
            unit_costs = {"N": 0}
            unit_block_ids = {"N": None}
            for label, selection_state in (
                ("A", "A"),
                ("B", "B"),
                ("X", "BOTH"),
            ):
                candidates = [
                    (
                        confidential_hmac_rank(
                            secret=secret,
                            domain=SELECTION_HMAC_DOMAIN,
                            context={
                                **dict(hmac_context),
                                "block_identity": block.block_id,
                                "selection_state": selection_state,
                            },
                        ),
                        str(block.block_id),
                    )
                    for block in unit
                ]
                rank, block_id = min(candidates)
                unit_costs[label] = rank
                unit_block_ids[label] = block_id
            costs.append(unit_costs)
            block_ids.append(unit_block_ids)

        assignment_solvers.append(make_assignment_solver(rows, tuple(costs)))
        assignment_block_ids.append(tuple(block_ids))

    def make_alternative_type_solver(logical_units):
        zero_contributors = (0,) * len(contributor_order)
        unit_choices = []
        for rows in logical_units:
            best_transition = {}
            for block in rows:
                member = block.members[0]
                block_cells = [0] * len(cell_order)
                block_cells[
                    cell_index[(member.campus, member.difficulty)]
                ] = 1
                block_cells = tuple(block_cells)
                block_contributors = [0] * len(contributor_order)
                block_contributors[
                    contributor_index[member.contributor_id]
                ] = 1
                block_contributors = tuple(block_contributors)
                for use_a, use_b, label in (
                    (1, 0, "A"),
                    (0, 1, "B"),
                    (1, 1, "BOTH"),
                ):
                    rank = confidential_hmac_rank(
                        secret=secret,
                        domain=SELECTION_HMAC_DOMAIN,
                        context={
                            **dict(hmac_context),
                            "block_identity": block.block_id,
                            "selection_state": label,
                        },
                    )
                    transition_key = (
                        use_a,
                        use_b,
                        block.vector,
                        block_cells,
                        block_contributors,
                    )
                    candidate = (rank, str(block.block_id))
                    current = best_transition.get(transition_key)
                    if current is None or candidate < current:
                        best_transition[transition_key] = candidate
            choices = [
                (
                    0,
                    0,
                    (),
                    (),
                    zero_vector,
                    zero_vector,
                    zero_cells,
                    zero_cells,
                    zero_contributors,
                    0,
                )
            ]
            for (
                use_a,
                use_b,
                block_vector,
                block_cells,
                block_contributors,
            ), (rank, block_id) in best_transition.items():
                choices.append(
                    (
                        use_a,
                        use_b,
                        (block_id,) if use_a else (),
                        (block_id,) if use_b else (),
                        tuple(value * use_a for value in block_vector),
                        tuple(value * use_b for value in block_vector),
                        tuple(value * use_a for value in block_cells),
                        tuple(value * use_b for value in block_cells),
                        tuple(
                            value * (use_a + use_b)
                            for value in block_contributors
                        ),
                        rank,
                    )
                )
            unit_choices.append(tuple(choices))

        def solve():
            initial_key = (
                zero_vector,
                zero_vector,
                zero_cells,
                zero_cells,
                zero_contributors,
                0,
            )
            states = {initial_key: (0, (), ())}
            for choices in unit_choices:
                next_states = {}
                for state, evidence in states.items():
                    count_state()
                    (
                        vector_a,
                        vector_b,
                        cells_a,
                        cells_b,
                        contributors,
                        overlap_used,
                    ) = state
                    hmac_used, selected_a, selected_b = evidence
                    for (
                        use_a,
                        use_b,
                        chosen_a,
                        chosen_b,
                        delta_vector_a,
                        delta_vector_b,
                        delta_cells_a,
                        delta_cells_b,
                        delta_contributors,
                        rank,
                    ) in choices:
                        next_vector_a = tuple(
                            vector_a[position] + delta_vector_a[position]
                            for position in range(dimension_count)
                        )
                        next_vector_b = tuple(
                            vector_b[position] + delta_vector_b[position]
                            for position in range(dimension_count)
                        )
                        if any(
                            next_vector_a[position] > margins[position]
                            or next_vector_b[position] > margins[position]
                            for position in range(dimension_count)
                        ):
                            continue
                        next_overlap = overlap_used + (1 if use_a and use_b else 0)
                        if next_overlap > minimum_overlap:
                            continue
                        next_key = (
                            next_vector_a,
                            next_vector_b,
                            tuple(
                                cells_a[position] + delta_cells_a[position]
                                for position in range(len(cell_order))
                            ),
                            tuple(
                                cells_b[position] + delta_cells_b[position]
                                for position in range(len(cell_order))
                            ),
                            tuple(
                                contributors[position]
                                + delta_contributors[position]
                                for position in range(len(contributor_order))
                            ),
                            next_overlap,
                        )
                        next_evidence = (
                            hmac_used + rank,
                            tuple(sorted(selected_a + chosen_a)),
                            tuple(sorted(selected_b + chosen_b)),
                        )
                        current = next_states.get(next_key)
                        if current is None or next_evidence < current:
                            next_states[next_key] = next_evidence
                states = next_states
            return tuple(
                (
                    overlap_used,
                    evidence[0],
                    vector_a[0],
                    vector_b[0],
                    evidence[1],
                    evidence[2],
                    vector_a,
                    vector_b,
                    cells_a,
                    cells_b,
                    contributors,
                )
                for (
                    vector_a,
                    vector_b,
                    cells_a,
                    cells_b,
                    contributors,
                    overlap_used,
                ), evidence in states.items()
            )

        return solve

    alternative_type_solvers = [
        (
            make_alternative_type_solver(group[2])
            if group[0] == "alternative_type"
            else None
        )
        for group in groups
    ]

    seen_hmac = {}
    best_score = None
    best_selection = None
    selected_a = []
    selected_b = []

    class AutomaticPrimaryOptimumFound(Exception):
        pass

    class HardFeasibleSelectionFound(Exception):
        pass

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
        proportional_bound = proportional_lower_bound(index, cells_a, cells_b)
        if best_score is not None:
            if proportional_bound > best_score[0]:
                return
            if proportional_bound == best_score[0]:
                future_contributors = suffix_contributors[index]
                maximum_represented = sum(
                    bool(appearances[position] or future_contributors[position])
                    for position in range(len(contributor_order))
                )
                best_represented = -best_score[1]
                if maximum_represented < best_represented:
                    return
                if maximum_represented == best_represented:
                    optimistic_counts = list(appearances)
                    for _offset in range(remaining_a[0] + remaining_b[0]):
                        target = min(
                            range(len(optimistic_counts)),
                            key=lambda position: optimistic_counts[position],
                        )
                        optimistic_counts[target] += 1
                    concentration_bound = sum(
                        count * count for count in optimistic_counts
                    )
                    if concentration_bound > best_score[2]:
                        return
                    if (
                        concentration_bound == best_score[2]
                        and hmac_total > best_score[3]
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
                if stop_at_first_feasible:
                    raise HardFeasibleSelectionFound
                if (
                    automatic_primary_bound is not None
                    and score[:3] == automatic_primary_bound
                ):
                    raise AutomaticPrimaryOptimumFound
            return

        kind, group_blocks = groups[index][:2]
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
                        tuple(value * use_a for value in block.vector),
                        tuple(value * use_b for value in block.vector),
                        tuple(value * use_a for value in block_cells),
                        tuple(value * use_b for value in block_cells),
                        tuple(
                            value * (use_a + use_b)
                            for value in block_contributors
                        ),
                    )
                )
        elif kind == "alternative_type":
            alternative_decisions = alternative_type_solvers[index]()
            decisions.extend(
                decision
                for decision in alternative_decisions
                if overlap + decision[0] <= minimum_overlap
                and all(
                    decision[6][position] <= remaining_a[position]
                    and decision[7][position] <= remaining_b[position]
                    for position in range(dimension_count)
                )
            )
        else:
            capacity = (
                len(groups[index][2])
                if kind == "logical_singletons"
                else len(group_blocks)
            )
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
                        block_ids = assignment_block_ids[index]
                        chosen_a = tuple(
                            block_ids[position][label]
                            for position, label in enumerate(labels)
                            if label in ("A", "X")
                        )
                        chosen_b = tuple(
                            block_ids[position][label]
                            for position, label in enumerate(labels)
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
                                tuple(value * amount_a for value in unit),
                                tuple(value * amount_b for value in unit),
                                tuple(
                                    value * amount_a
                                    for value in group_cell_vectors[index]
                                ),
                                tuple(
                                    value * amount_b
                                    for value in group_cell_vectors[index]
                                ),
                                tuple(
                                    value * (amount_a + amount_b)
                                    for value in group_contributor_vectors[index]
                                ),
                            )
                        )
        future = suffix[index + 1]
        filtered_decisions = []
        for decision in decisions:
            next_a = subtract_multiple(remaining_a, decision[6], 1)
            next_b = subtract_multiple(remaining_b, decision[7], 1)
            if any(
                value < 0 or value > future[position]
                for position, value in enumerate(next_a)
            ) or any(
                value < 0 or value > future[position]
                for position, value in enumerate(next_b)
            ):
                continue
            unavoidable = max(0, next_a[0] + next_b[0] - future[0])
            if overlap + decision[0] + unavoidable > minimum_overlap:
                continue
            filtered_decisions.append(decision)
        decisions = filtered_decisions
        decisions.sort(
            key=lambda row: (
                proportional_lower_bound(
                    index + 1,
                    add_vector(cells_a, row[8]),
                    add_vector(cells_b, row[9]),
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
            vector_a,
            vector_b,
            cells_delta_a,
            cells_delta_b,
            contributor_delta,
        ) in decisions:
            selected_a.extend(chosen_a)
            selected_b.extend(chosen_b)
            search(
                index + 1,
                subtract_multiple(remaining_a, vector_a, 1),
                subtract_multiple(remaining_b, vector_b, 1),
                overlap + local_overlap,
                add_vector(cells_a, cells_delta_a),
                add_vector(cells_b, cells_delta_b),
                add_vector(appearances, contributor_delta),
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
    except AutomaticPrimaryOptimumFound:
        pass
    except HardFeasibleSelectionFound:
        pass
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


def solve_automatic_identity_aware_two_sets(
    *,
    margins,
    blocks,
    campus_quotas,
    difficulty_quotas,
    secret,
    hmac_context,
    max_states,
    optimize_soft=False,
):
    """Find the minimum-overlap Automatic selection with a hard-feasible gate.

    The identity-aware selector is authoritative for both readiness and output.
    The assignment fast path keeps campus/difficulty proportionality and stable
    HMAC ordering deterministic. Contributor metrics are descriptive on the
    hard path. Output may attempt the existing soft objectives afterward, but
    exhausting that bounded refinement returns the proved hard selection.
    """
    margins = tuple(int(value) for value in margins)
    if not margins or margins[0] < 0:
        return IdentitySelectionResult(False, False, 0)
    logical_ids = {
        str(block.logical_group_id)
        for block in blocks
        if block.logical_group_id is not None
    }
    if any(
        block.size != 1 or block.logical_group_id is None
        for block in blocks
    ):
        raise ValueError("Automatic logical selections must be singleton blocks.")
    minimum_possible_overlap = max(0, 2 * margins[0] - len(logical_ids))
    for campus, quota in campus_quotas.items():
        available_groups = len(
            {
                str(block.logical_group_id)
                for block in blocks
                if block.members[0].campus == campus
            }
        )
        minimum_possible_overlap = max(
            minimum_possible_overlap,
            2 * int(quota) - available_groups,
        )
    for difficulty, quota in difficulty_quotas.items():
        available_groups = len(
            {
                str(block.logical_group_id)
                for block in blocks
                if block.members[0].difficulty == difficulty
            }
        )
        minimum_possible_overlap = max(
            minimum_possible_overlap,
            2 * int(quota) - available_groups,
        )
    states_explored = 0
    hard_selection = None
    for overlap in range(minimum_possible_overlap, margins[0] + 1):
        remaining_states = int(max_states) - states_explored
        if remaining_states <= 0:
            return IdentitySelectionResult(False, True, states_explored)
        candidate = solve_identity_aware_two_sets(
            margins=margins,
            blocks=blocks,
            minimum_overlap=overlap,
            campus_quotas=campus_quotas,
            difficulty_quotas=difficulty_quotas,
            secret=secret,
            hmac_context={**dict(hmac_context), "minimum_overlap": overlap},
            max_states=remaining_states,
            stop_at_first_feasible=True,
        )
        states_explored += candidate.states_explored
        if candidate.limit_hit:
            return IdentitySelectionResult(False, True, states_explored)
        if candidate.feasible:
            hard_selection = IdentitySelectionResult(
                feasible=True,
                limit_hit=False,
                states_explored=states_explored,
                set_a_block_ids=candidate.set_a_block_ids,
                set_b_block_ids=candidate.set_b_block_ids,
                overlap=candidate.overlap,
                proportional_score=candidate.proportional_score,
                contributors_represented=candidate.contributors_represented,
                squared_contributor_concentration=(
                    candidate.squared_contributor_concentration
                ),
            )
            break
    if hard_selection is None or not optimize_soft:
        return hard_selection or IdentitySelectionResult(
            False, False, states_explored
        )

    remaining_states = int(max_states) - states_explored
    if remaining_states <= 0:
        return hard_selection
    optimized = solve_identity_aware_two_sets(
        margins=margins,
        blocks=blocks,
        minimum_overlap=hard_selection.overlap,
        campus_quotas=campus_quotas,
        difficulty_quotas=difficulty_quotas,
        secret=secret,
        hmac_context=dict(hmac_context),
        max_states=min(remaining_states, 100),
    )
    if optimized.feasible:
        return IdentitySelectionResult(
            feasible=True,
            limit_hit=False,
            states_explored=states_explored + optimized.states_explored,
            set_a_block_ids=optimized.set_a_block_ids,
            set_b_block_ids=optimized.set_b_block_ids,
            overlap=optimized.overlap,
            proportional_score=optimized.proportional_score,
            contributors_represented=optimized.contributors_represented,
            squared_contributor_concentration=(
                optimized.squared_contributor_concentration
            ),
        )
    return IdentitySelectionResult(
        feasible=True,
        limit_hit=False,
        states_explored=states_explored + optimized.states_explored,
        set_a_block_ids=hard_selection.set_a_block_ids,
        set_b_block_ids=hard_selection.set_b_block_ids,
        overlap=hard_selection.overlap,
        proportional_score=hard_selection.proportional_score,
        contributors_represented=hard_selection.contributors_represented,
        squared_contributor_concentration=(
            hard_selection.squared_contributor_concentration
        ),
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
    alternative_vector_groups=(),
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
    alternative_counts = {}
    for group in alternative_vector_groups:
        options = tuple(
            sorted({tuple(int(value) for value in vector) for vector in group})
        )
        alternative_counts[options] = alternative_counts.get(options, 0) + 1
    alternatives = tuple(
        sorted(
            alternative_counts.items(),
            key=lambda item: (len(item[0]), item[0], item[1]),
        )
    )
    if any(len(vector) != dimension_count for vector in scenarios):
        raise ValueError("Scenario vector dimension does not match margins.")
    if any(len(vector) != dimension_count for vector, _capacity in cells):
        raise ValueError("Singleton vector dimension does not match margins.")
    if any(
        not options or any(len(vector) != dimension_count for vector in options)
        for options, _capacity in alternatives
    ):
        raise ValueError("Alternative vector dimension does not match margins.")
    if any(any(value < 0 for value in vector) for vector in scenarios):
        raise ValueError("Scenario vectors cannot contain negative values.")
    if any(
        any(value not in (0, 1) for value in vector) or vector[0] != 1
        for vector, _capacity in cells
    ):
        raise ValueError("Singleton vectors must be zero/one vectors with total one.")
    if any(
        any(any(value not in (0, 1) for value in vector) or vector[0] != 1 for vector in options)
        for options, _capacity in alternatives
    ):
        raise ValueError(
            "Alternative vectors must be zero/one vectors with total one."
        )

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

    alternative_suffix = [zero for _ in range(len(alternatives) + 1)]
    for index in range(len(alternatives) - 1, -1, -1):
        options, capacity = alternatives[index]
        available = tuple(
            max(vector[position] for vector in options) * capacity
            for position in range(dimension_count)
        )
        alternative_suffix[index] = tuple(
            alternative_suffix[index + 1][position] + available[position]
            for position in range(dimension_count)
        )

    @lru_cache(maxsize=None)
    def alternative_type_decisions(index):
        options, capacity = alternatives[index]
        unit_decisions = [(0, zero, zero)]
        for vector in options:
            unit_decisions.extend(
                (
                    (0, vector, zero),
                    (0, zero, vector),
                    (1, vector, vector),
                )
            )
        states = {(zero, zero): 0}
        for _offset in range(capacity):
            next_states = {}
            for (used_a, used_b), overlap_used in states.items():
                count_state()
                for local_overlap, vector_a, vector_b in unit_decisions:
                    next_a = tuple(
                        used_a[position] + vector_a[position]
                        for position in range(dimension_count)
                    )
                    next_b = tuple(
                        used_b[position] + vector_b[position]
                        for position in range(dimension_count)
                    )
                    if any(
                        next_a[position] > margins[position]
                        or next_b[position] > margins[position]
                        for position in range(dimension_count)
                    ):
                        continue
                    if next_b < next_a:
                        next_a, next_b = next_b, next_a
                    key = (next_a, next_b)
                    candidate_overlap = overlap_used + local_overlap
                    previous = next_states.get(key)
                    if previous is None or candidate_overlap < previous:
                        next_states[key] = candidate_overlap
            states = next_states
        decisions = []
        for (used_a, used_b), overlap_used in states.items():
            decisions.append((overlap_used, used_a, used_b))
            if used_a != used_b:
                decisions.append((overlap_used, used_b, used_a))
        return tuple(decisions)

    @lru_cache(maxsize=None)
    def solve_alternatives(index, remaining_a, remaining_b):
        count_state()
        if remaining_b < remaining_a:
            return solve_alternatives(index, remaining_b, remaining_a)
        available = tuple(
            alternative_suffix[index][position]
            + scenario_suffix[0][position]
            + cell_suffix[0][position]
            for position in range(dimension_count)
        )
        if any(
            remaining_a[position] > available[position]
            or remaining_b[position] > available[position]
            for position in range(dimension_count)
        ):
            return None
        if index == len(alternatives):
            return solve_scenarios(0, remaining_a, remaining_b)
        best = None
        future = tuple(
            alternative_suffix[index + 1][position]
            + scenario_suffix[0][position]
            + cell_suffix[0][position]
            for position in range(dimension_count)
        )
        decisions = []
        for local_overlap, vector_a, vector_b in alternative_type_decisions(index):
            if any(
                vector_a[position] > remaining_a[position]
                or vector_b[position] > remaining_b[position]
                for position in range(dimension_count)
            ):
                continue
            next_a = tuple(
                remaining_a[position] - vector_a[position]
                for position in range(dimension_count)
            )
            next_b = tuple(
                remaining_b[position] - vector_b[position]
                for position in range(dimension_count)
            )
            if any(
                next_a[position] > future[position]
                or next_b[position] > future[position]
                for position in range(dimension_count)
            ):
                continue
            decisions.append(
                (local_overlap, vector_a, vector_b, next_a, next_b)
            )
        decisions.sort(
            key=lambda item: (
                item[0],
                sum(
                    abs(item[3][position] - item[4][position])
                    for position in range(dimension_count)
                ),
                item[3],
                item[4],
                item[1],
                item[2],
            )
        )
        for local_overlap, vector_a, vector_b, next_a, next_b in decisions:
            if best is not None and local_overlap >= best:
                continue
            if next_b < next_a:
                next_a, next_b = next_b, next_a
            tail = solve_alternatives(index + 1, next_a, next_b)
            if tail is None:
                continue
            candidate = local_overlap + tail
            if best is None or candidate < best:
                best = candidate
                if best == 0:
                    break
        return best

    try:
        minimum_overlap = solve_alternatives(0, margins, margins)
    except FeasibilityLimitExceeded:
        return FeasibilityResult(False, None, state_counter, limit_hit=True)
    return FeasibilityResult(
        feasible=minimum_overlap is not None,
        minimum_overlap=minimum_overlap,
        states_explored=state_counter,
    )
