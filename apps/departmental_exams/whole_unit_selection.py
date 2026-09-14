"""Budgeted Automatic selection; Cases are indivisible and identities are reserved.

Only units with no identity conflicts anywhere in the pool are compressed, and
only when their hard vector and all soft-score contributions match. Alternative
copies of a logical question use one representative across both sets, matching
the flat Automatic contract. The singleton fast path remains unchanged.
"""
from collections import Counter, defaultdict

from .generation_algorithms import (
    FeasibilityLimitExceeded, IdentitySelectionResult, confidential_hmac_rank,
    proportional_campus_difficulty_score,
)


def unreachable_margins(*, margins, blocks):
    """Cheap necessary subset-sum checks; never claim joint feasibility."""
    failures = []
    for position, quota in enumerate(margins):
        reachable = 1
        mask = (1 << (quota + 1)) - 1
        for block in blocks:
            reachable = (reachable | (reachable << block.vector[position])) & mask
        if not (reachable >> quota) & 1:
            failures.append(position)
    return tuple(failures)


def solve_whole_unit_two_sets(*, margins, blocks, campus_quotas, difficulty_quotas,
                             secret, hmac_context, max_states, optimize_soft=True):
    margins = tuple(margins)
    blocks = tuple(blocks)
    total = margins[0]
    states = 0

    def spend():
        nonlocal states
        if states >= max_states:
            raise FeasibilityLimitExceeded
        states += 1

    def identities(block):
        return block.logical_fingerprints or (block.logical_group_id,)

    occurrence = Counter(identity for block in blocks for identity in identities(block))
    if any(len(identities(b)) != b.size or None in identities(b)
           or len(set(identities(b))) != b.size or b.vector[0] != b.size
           or len(b.vector) != len(margins) for b in blocks):
        raise ValueError("Whole units require complete, distinct member identities and hard vectors.")
    if len({b.block_id for b in blocks}) != len(blocks):
        raise ValueError("Selection block identities must be unique.")
    if max_states < 1:
        return IdentitySelectionResult(False, True, 0)
    if unreachable_margins(margins=margins, blocks=blocks):
        return IdentitySelectionResult(False, False, 0)
    grouped = defaultdict(list)
    for block in blocks:
        score_signature = tuple(sorted(Counter(
            (m.campus, m.difficulty, m.contributor_id) for m in block.members
        ).items()))
        conflict = any(occurrence[i] > 1 for i in identities(block))
        key = (block.vector, score_signature, block.block_id if conflict else None)
        grouped[key].append(block)

    def rank(block):
        return (confidential_hmac_rank(secret=secret, domain="automatic.whole-unit.v1",
                                      context={**hmac_context, "block": block.block_id}),
                str(block.block_id))

    groups = [tuple(sorted(rows, key=rank)) for rows in grouped.values()]
    groups.sort(key=lambda rows: (-rows[0].size, rows[0].vector, rank(rows[0])))
    suffix = [tuple(0 for _ in margins) for _ in range(len(groups) + 1)]
    for i in range(len(groups) - 1, -1, -1):
        suffix[i] = tuple(suffix[i + 1][j] + len(groups[i]) * groups[i][0].vector[j]
                          for j in range(len(margins)))

    def witnesses(overlap, *, first_only):
        # Explicit iterator stack avoids recursion limits and materializing the
        # Cartesian product of group allocations. Every attempted branch costs
        # one state, including branches rejected by identity reservations.
        visited = set()

        def options(index, remaining_a, remaining_b, shared, used, choices):
            spend()
            if any(a < 0 or b < 0 or max(a, b) > suffix[index][j]
                   for j, (a, b) in enumerate(zip(remaining_a, remaining_b))):
                return
            if shared < 0 or shared > min(remaining_a[0], remaining_b[0]):
                return
            if remaining_a[0] + remaining_b[0] - shared > suffix[index][0]:
                return
            state = (index, remaining_a, remaining_b, shared, used)
            if first_only:
                if state in visited:
                    return
                if len(visited) < 50000:
                    visited.add(state)
            if index == len(groups):
                if not any(remaining_a + remaining_b) and not shared:
                    yield choices
                return
            rows = groups[index]
            vector = rows[0].vector
            capacity = len(rows)
            max_a = min([capacity] + [a // v for a, v in zip(remaining_a, vector) if v])
            max_b = min([capacity] + [b // v for b, v in zip(remaining_b, vector) if v])
            for count_a in range(max_a, -1, -1):
                for count_b in range(max_b, -1, -1):
                    minimum_shared = max(0, count_a + count_b - capacity)
                    maximum_shared = min(count_a, count_b, shared // rows[0].size)
                    for both in range(minimum_shared, maximum_shared + 1):
                        spend()
                        union_count = count_a + count_b - both
                        selected = rows[:union_count]
                        reserved = frozenset(i for row in selected for i in identities(row)
                                             if occurrence[i] > 1)
                        if used & reserved:
                            continue
                        yield (index + 1,
                               tuple(a - count_a * v for a, v in zip(remaining_a, vector)),
                               tuple(b - count_b * v for b, v in zip(remaining_b, vector)),
                               shared - both * rows[0].size, used | reserved,
                               choices + ((count_a, count_b, both),))

        stack = [iter(options(0, margins, margins, overlap, frozenset(), ()))]
        while stack:
            try:
                next_state = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            if len(next_state) == len(groups) and all(
                isinstance(entry, tuple) and len(entry) == 3 for entry in next_state
            ):
                yield next_state
            else:
                stack.append(iter(options(*next_state)))

    def result(choices, overlap):
        selected = [[], []]
        for rows, (count_a, count_b, both) in zip(groups, choices):
            selected[0].extend(rows[:count_a])
            selected[1].extend(rows[:both] + rows[count_a:count_a + count_b - both])
        deviation, proportional = 0, 0
        contributors = Counter()
        for rows in selected:
            members = [member for row in rows for member in row.members]
            counts = Counter(m.difficulty for m in members)
            deviation += sum(abs(counts[d] - n) for d, n in difficulty_quotas.items())
            proportional += proportional_campus_difficulty_score(
                total=total, campus_quotas=campus_quotas, difficulty_quotas=difficulty_quotas,
                cell_counts=Counter((m.campus, m.difficulty) for m in members))
            contributors.update(m.contributor_id for m in members)
        ids = tuple(tuple(str(row.block_id) for row in rows) for rows in selected)
        score = (deviation, proportional, -len(contributors),
                 sum(n * n for n in contributors.values()), ids)
        return score, ids, contributors

    best = None
    lower = max(0, 2 * total - len(occurrence))
    try:
        for overlap in range(lower, total + 1):
            candidate = next(witnesses(overlap, first_only=True), None)
            if candidate is not None:
                best = result(candidate, overlap)
                break
    except FeasibilityLimitExceeded:
        return IdentitySelectionResult(False, True, states)
    if best is None:
        return IdentitySelectionResult(False, False, states)
    soft_limit = False
    proved = best[0][0] == 0
    if optimize_soft and not proved:
        try:
            for candidate in witnesses(overlap, first_only=False):
                trial = result(candidate, overlap)
                if trial[0] < best[0]:
                    best = trial
                if best[0][0] == 0:
                    break
            proved = True
        except FeasibilityLimitExceeded:
            soft_limit = True
    score, ids, contributors = best
    return IdentitySelectionResult(
        True, False, states, ids[0], ids[1], overlap, score[1], len(contributors),
        sum(n * n for n in contributors.values()), score[0], score[0] == 0,
        proved, soft_limit,
    )
