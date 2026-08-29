from collections import Counter
from itertools import combinations
from time import perf_counter
from unittest.mock import patch

from django.test import SimpleTestCase

from .generation_algorithms import (
    AllocationError,
    IdentityBlock,
    IdentityMember,
    allocate_campuses,
    allocate_difficulties,
    confidential_hmac_rank,
    order_selected_blocks,
    proportional_campus_difficulty_score,
    solve_automatic_identity_aware_two_sets,
    solve_identity_aware_two_sets,
    solve_two_set_feasibility,
)


class Stage6HamiltonAllocationTests(SimpleTestCase):
    def test_three_campus_approved_outputs(self):
        campuses = ("CUBAO", "FAIRVIEW", "TAYTAY")
        self.assertEqual(allocate_campuses(50, campuses), {"CUBAO": 17, "FAIRVIEW": 16, "TAYTAY": 17})
        self.assertEqual(allocate_campuses(60, campuses), {"CUBAO": 20, "FAIRVIEW": 20, "TAYTAY": 20})
        self.assertEqual(allocate_campuses(70, campuses), {"CUBAO": 23, "FAIRVIEW": 23, "TAYTAY": 24})
        self.assertEqual(allocate_campuses(75, campuses), {"CUBAO": 25, "FAIRVIEW": 25, "TAYTAY": 25})

    def test_reduced_and_single_campus_allocations_are_exact_and_deterministic(self):
        self.assertEqual(allocate_campuses(50, ("CUBAO",)), {"CUBAO": 50})
        self.assertEqual(allocate_campuses(51, ("CUBAO", "FAIRVIEW")), {"CUBAO": 26, "FAIRVIEW": 25})
        self.assertEqual(allocate_campuses(50, ("CUBAO", "TAYTAY")), {"CUBAO": 25, "TAYTAY": 25})
        self.assertEqual(
            allocate_campuses(51, ("FAIRVIEW", "CUBAO")),
            allocate_campuses(51, ("CUBAO", "FAIRVIEW")),
        )

    def test_unknown_campus_fails_closed(self):
        with self.assertRaisesRegex(AllocationError, "Unknown participating campus"):
            allocate_campuses(50, ("CUBAO", "UNKNOWN"))

    def test_difficulty_approved_outputs_and_tie_priority(self):
        self.assertEqual(allocate_difficulties(50), {"EASY": 15, "MODERATE": 25, "DIFFICULT": 10})
        self.assertEqual(allocate_difficulties(60), {"EASY": 18, "MODERATE": 30, "DIFFICULT": 12})
        self.assertEqual(allocate_difficulties(70), {"EASY": 21, "MODERATE": 35, "DIFFICULT": 14})
        self.assertEqual(allocate_difficulties(75), {"EASY": 22, "MODERATE": 38, "DIFFICULT": 15})
        self.assertEqual(allocate_difficulties(75)["MODERATE"], 38)


class Stage6FeasibilityAlgorithmTests(SimpleTestCase):
    # Vector order: total, campus A/B, difficulty A/B, one section.
    margins = (2, 1, 1, 1, 1, 2)
    cell_a = (1, 1, 0, 1, 0, 1)
    cell_b = (1, 0, 1, 0, 1, 1)

    def test_representative_50_60_70_75_hard_margin_cases(self):
        campuses = ("CUBAO", "FAIRVIEW", "TAYTAY")
        difficulties = ("EASY", "MODERATE", "DIFFICULT")
        for total in (50, 60, 70, 75):
            with self.subTest(total=total):
                campus_remaining = allocate_campuses(total, campuses)
                difficulty_remaining = allocate_difficulties(total)
                capacities = {}
                campus_index = 0
                difficulty_index = 0
                while campus_index < len(campuses) and difficulty_index < len(difficulties):
                    campus = campuses[campus_index]
                    difficulty = difficulties[difficulty_index]
                    count = min(campus_remaining[campus], difficulty_remaining[difficulty])
                    if count:
                        vector = (
                            1,
                            *(1 if code == campus else 0 for code in campuses),
                            *(1 if code == difficulty else 0 for code in difficulties),
                            1,
                        )
                        capacities[vector] = count * 2
                        campus_remaining[campus] -= count
                        difficulty_remaining[difficulty] -= count
                    if campus_remaining[campus] == 0:
                        campus_index += 1
                    if difficulty_remaining[difficulty] == 0:
                        difficulty_index += 1

                margins = (
                    total,
                    *(allocate_campuses(total, campuses)[code] for code in campuses),
                    *(allocate_difficulties(total)[code] for code in difficulties),
                    total,
                )
                result = solve_two_set_feasibility(
                    margins=margins,
                    scenario_vectors=(),
                    singleton_capacities=capacities,
                )
                self.assertTrue(result.feasible)
                self.assertEqual(result.minimum_overlap, 0)

    def test_zero_overlap_is_preferred_when_disjoint_capacity_exists(self):
        result = solve_two_set_feasibility(
            margins=self.margins,
            scenario_vectors=(),
            singleton_capacities={self.cell_a: 2, self.cell_b: 2},
        )
        self.assertTrue(result.feasible)
        self.assertEqual(result.minimum_overlap, 0)

    def test_reuse_is_exactly_the_minimum_necessary(self):
        result = solve_two_set_feasibility(
            margins=self.margins,
            scenario_vectors=(),
            singleton_capacities={self.cell_a: 1, self.cell_b: 1},
        )
        self.assertTrue(result.feasible)
        self.assertEqual(result.minimum_overlap, 2)

    def test_scenario_is_atomic_and_may_be_reused_only_as_one_bundle(self):
        result = solve_two_set_feasibility(
            margins=self.margins,
            scenario_vectors=((2, 1, 1, 1, 1, 2),),
            singleton_capacities={},
        )
        self.assertTrue(result.feasible)
        self.assertEqual(result.minimum_overlap, 2)

    def test_sparse_cross_margin_pool_is_infeasible(self):
        result = solve_two_set_feasibility(
            margins=self.margins,
            scenario_vectors=(),
            singleton_capacities={self.cell_a: 10},
        )
        self.assertFalse(result.feasible)
        self.assertIsNone(result.minimum_overlap)

    def test_repeated_runs_are_deterministic(self):
        args = {
            "margins": self.margins,
            "scenario_vectors": (),
            "singleton_capacities": {self.cell_a: 2, self.cell_b: 2},
        }
        first = solve_two_set_feasibility(**args)
        second = solve_two_set_feasibility(**args)
        self.assertEqual(first, second)

    def test_deterministic_state_limit_is_reported(self):
        result = solve_two_set_feasibility(
            margins=self.margins,
            scenario_vectors=(),
            singleton_capacities={self.cell_a: 2, self.cell_b: 2},
            max_states=1,
        )
        self.assertTrue(result.limit_hit)
        self.assertFalse(result.feasible)


class Stage6IdentityAwareSelectionTests(SimpleTestCase):
    secret = "stage6b-test-secret"

    @staticmethod
    def block(source_id, contributor_id, *, campus="CUBAO", difficulty="EASY", section=0):
        return IdentityBlock(
            block_id=f"question:{source_id}",
            vector=(1, 1, 1, 1),
            members=(
                IdentityMember(
                    source_id=source_id,
                    contributor_id=contributor_id,
                    campus=campus,
                    difficulty=difficulty,
                    section_id=section,
                ),
            ),
        )

    def solve(self, blocks, *, total=1, overlap=0, max_states=100_000):
        return solve_identity_aware_two_sets(
            margins=(total, total, total, total),
            blocks=blocks,
            minimum_overlap=overlap,
            campus_quotas={"CUBAO": total},
            difficulty_quotas={"EASY": total},
            secret=self.secret,
            hmac_context={"input_fingerprint": "a" * 64},
            max_states=max_states,
        )

    def test_zero_overlap_and_candidate_iteration_order_independence(self):
        blocks = [self.block(1, 1), self.block(2, 2), self.block(3, 3)]
        first = self.solve(blocks)
        second = self.solve(list(reversed(blocks)))
        self.assertTrue(first.feasible)
        self.assertEqual(first.overlap, 0)
        self.assertEqual(first, second)
        self.assertTrue(set(first.set_a_block_ids).isdisjoint(first.set_b_block_ids))

    def test_mathematically_required_overlap_is_exact(self):
        result = self.solve([self.block(1, 1)], overlap=1)
        self.assertTrue(result.feasible)
        self.assertEqual(result.set_a_block_ids, ("question:1",))
        self.assertEqual(result.set_b_block_ids, ("question:1",))
        self.assertEqual(result.overlap, 1)
        self.assertEqual(result.squared_contributor_concentration, 4)

    def test_contributor_representation_then_squared_concentration(self):
        represented = self.solve(
            [self.block(1, 1), self.block(2, 1), self.block(3, 2)]
        )
        self.assertEqual(represented.contributors_represented, 2)

        balanced = self.solve(
            [
                self.block(1, 1),
                self.block(2, 1),
                self.block(3, 1),
                self.block(4, 2),
                self.block(5, 2),
            ],
            total=2,
        )
        self.assertTrue(balanced.feasible)
        self.assertEqual(balanced.contributors_represented, 2)
        self.assertEqual(balanced.squared_contributor_concentration, 8)

    def test_hard_constraints_and_overlap_override_fairness(self):
        only_one_contributor = self.solve(
            [self.block(1, 7), self.block(2, 7)]
        )
        self.assertTrue(only_one_contributor.feasible)
        self.assertEqual(only_one_contributor.contributors_represented, 1)
        self.assertEqual(only_one_contributor.overlap, 0)

    def test_state_limit_returns_no_unproved_incumbent(self):
        result = self.solve(
            [self.block(1, 1), self.block(2, 2)],
            max_states=1,
        )
        self.assertTrue(result.limit_hit)
        self.assertFalse(result.feasible)
        self.assertEqual(result.set_a_block_ids, ())

    def test_scenario_is_atomic_contiguous_and_members_count_individually(self):
        scenario = IdentityBlock(
            block_id="scenario:8",
            vector=(2, 2, 2, 2),
            members=(
                IdentityMember(1, 10, "CUBAO", "EASY", 0, 1),
                IdentityMember(2, 11, "CUBAO", "EASY", 0, 2),
            ),
        )
        result = self.solve([scenario], total=2, overlap=2)
        self.assertTrue(result.feasible)
        self.assertEqual(result.contributors_represented, 2)
        self.assertEqual(result.squared_contributor_concentration, 8)
        ordered = order_selected_blocks(
            blocks=[scenario],
            selected_block_ids=result.set_a_block_ids,
            set_code="A",
            secret=self.secret,
            hmac_context={"input_fingerprint": "a" * 64},
        )
        self.assertEqual([member.source_id for member in ordered], [1, 2])

    def test_proportional_score_is_exact_integer_and_label_symmetric(self):
        campus = {"CUBAO": 1, "FAIRVIEW": 1}
        difficulty = {"EASY": 1, "MODERATE": 1}
        diagonal = {
            ("CUBAO", "EASY"): 1,
            ("CUBAO", "MODERATE"): 0,
            ("FAIRVIEW", "EASY"): 0,
            ("FAIRVIEW", "MODERATE"): 1,
        }
        score = proportional_campus_difficulty_score(
            total=2,
            campus_quotas=campus,
            difficulty_quotas=difficulty,
            cell_counts=diagonal,
        )
        permuted = proportional_campus_difficulty_score(
            total=2,
            campus_quotas={"FAIRVIEW": 1, "CUBAO": 1},
            difficulty_quotas={"MODERATE": 1, "EASY": 1},
            cell_counts=diagonal,
        )
        self.assertEqual(score, 4)
        self.assertEqual(score, permuted)
        self.assertIsInstance(score, int)

    def test_hmac_domain_separation_and_repeated_determinism(self):
        context = {"candidate": 4, "revision": 2}
        selection = confidential_hmac_rank(
            secret=self.secret,
            domain="departmental-exams.stage6b.selection",
            context=context,
        )
        order_a = confidential_hmac_rank(
            secret=self.secret,
            domain="departmental-exams.stage6b.order.set-a",
            context=context,
        )
        order_b = confidential_hmac_rank(
            secret=self.secret,
            domain="departmental-exams.stage6b.order.set-b",
            context=context,
        )
        self.assertNotEqual(selection, order_a)
        self.assertNotEqual(order_a, order_b)
        self.assertEqual(
            selection,
            confidential_hmac_rank(
                secret=self.secret,
                domain="departmental-exams.stage6b.selection",
                context=context,
            ),
        )

    def test_a_b_swap_preserves_every_higher_objective(self):
        blocks = [
            self.block(1, 1),
            self.block(2, 1),
            self.block(3, 2),
            self.block(4, 3),
        ]
        result = self.solve(blocks)
        contributors = {
            str(block.block_id): block.members[0].contributor_id for block in blocks
        }

        def symmetric_metrics(set_a, set_b):
            appearances = {}
            for block_id in set_a + set_b:
                contributor = contributors[block_id]
                appearances[contributor] = appearances.get(contributor, 0) + 1
            return (
                len(set(set_a).intersection(set_b)),
                len(appearances),
                sum(value * value for value in appearances.values()),
                tuple(sorted((len(set_a), len(set_b)))),
            )

        self.assertEqual(
            symmetric_metrics(result.set_a_block_ids, result.set_b_block_ids),
            symmetric_metrics(result.set_b_block_ids, result.set_a_block_ids),
        )
        self.assertEqual(result, self.solve(list(reversed(blocks))))

    def test_proportional_balance_overrides_better_contributor_representation(self):
        campuses = ("CUBAO", "FAIRVIEW")
        difficulties = ("EASY", "MODERATE")

        def member(source, contributor, campus, difficulty, order=1):
            return IdentityMember(source, contributor, campus, difficulty, 0, order)

        def vector_for(members):
            return (
                len(members),
                *(sum(row.campus == code for row in members) for code in campuses),
                *(sum(row.difficulty == code for row in members) for code in difficulties),
                len(members),
            )

        blocks = []
        source = 1
        for campus in campuses:
            for difficulty in difficulties:
                for _copy in range(2):
                    row = member(source, 1, campus, difficulty)
                    blocks.append(IdentityBlock(f"question:{source}", vector_for((row,)), (row,)))
                    source += 1
        diagonal = (
            member(source, 2, "CUBAO", "EASY", 1),
            member(source + 1, 3, "CUBAO", "EASY", 2),
            member(source + 2, 4, "FAIRVIEW", "MODERATE", 3),
            member(source + 3, 5, "FAIRVIEW", "MODERATE", 4),
        )
        source += 4
        off_diagonal = (
            member(source, 6, "CUBAO", "MODERATE", 1),
            member(source + 1, 7, "CUBAO", "MODERATE", 2),
            member(source + 2, 8, "FAIRVIEW", "EASY", 3),
            member(source + 3, 9, "FAIRVIEW", "EASY", 4),
        )
        blocks.extend(
            [
                IdentityBlock("scenario:1", vector_for(diagonal), diagonal),
                IdentityBlock("scenario:2", vector_for(off_diagonal), off_diagonal),
            ]
        )
        result = solve_identity_aware_two_sets(
            margins=(4, 2, 2, 2, 2, 4),
            blocks=blocks,
            minimum_overlap=0,
            campus_quotas={"CUBAO": 2, "FAIRVIEW": 2},
            difficulty_quotas={"EASY": 2, "MODERATE": 2},
            secret=self.secret,
            hmac_context={"input_fingerprint": "b" * 64},
        )
        self.assertTrue(result.feasible)
        self.assertEqual(result.proportional_score, 0)
        self.assertEqual(result.contributors_represented, 1)
        self.assertTrue(
            all(block_id.startswith("question:") for block_id in result.set_a_block_ids)
        )
        self.assertTrue(
            all(block_id.startswith("question:") for block_id in result.set_b_block_ids)
        )

    def test_representative_150_question_pool_proves_exact_50_item_selection(self):
        campuses = ("CUBAO", "FAIRVIEW", "TAYTAY")
        difficulties = ("EASY", "MODERATE", "DIFFICULT")
        source_difficulties = ["EASY"] * 15 + ["MODERATE"] * 25 + ["DIFFICULT"] * 10
        blocks = []
        source = 1
        for contributor, campus in enumerate(campuses, start=1):
            for difficulty in source_difficulties:
                vector = (
                    1,
                    *(1 if campus == code else 0 for code in campuses),
                    *(1 if difficulty == code else 0 for code in difficulties),
                    1,
                )
                blocks.append(
                    IdentityBlock(
                        f"question:{source}",
                        vector,
                        (IdentityMember(source, contributor, campus, difficulty, 0),),
                    )
                )
                source += 1
        campus_quotas = allocate_campuses(50, campuses)
        difficulty_quotas = allocate_difficulties(50)
        result = solve_identity_aware_two_sets(
            margins=(
                50,
                *(campus_quotas[code] for code in campuses),
                *(difficulty_quotas[code] for code in difficulties),
                50,
            ),
            blocks=blocks,
            minimum_overlap=0,
            campus_quotas=campus_quotas,
            difficulty_quotas=difficulty_quotas,
            secret=self.secret,
            hmac_context={"input_fingerprint": "c" * 64},
            max_states=500_000,
        )
        self.assertTrue(result.feasible)
        self.assertFalse(result.limit_hit)
        self.assertEqual(len(result.set_a_block_ids), 50)
        self.assertEqual(len(result.set_b_block_ids), 50)
        self.assertTrue(set(result.set_a_block_ids).isdisjoint(result.set_b_block_ids))
        self.assertLess(result.states_explored, 500_000)

    def test_automatic_sasa_shape_uses_shared_solver_without_state_limit(self):
        campuses = ("CUBAO", "FAIRVIEW", "TAYTAY")
        difficulties = ("EASY", "MODERATE", "DIFFICULT")
        campus_quotas = {"CUBAO": 17, "FAIRVIEW": 17, "TAYTAY": 16}
        difficulty_quotas = {"EASY": 15, "MODERATE": 25, "DIFFICULT": 10}
        fixed_capacities = {
            "CUBAO": {"DIFFICULT": 10, "EASY": 15, "MODERATE": 24},
            "FAIRVIEW": {"DIFFICULT": 10, "EASY": 15, "MODERATE": 23},
            "TAYTAY": {"EASY": 15, "MODERATE": 9},
        }
        logical_groups = []
        for campus in campuses:
            for difficulty, count in fixed_capacities[campus].items():
                logical_groups.extend([((campus, difficulty),)] * count)
        logical_groups.extend(
            [
                (("CUBAO", "MODERATE"), ("FAIRVIEW", "MODERATE")),
                (("FAIRVIEW", "MODERATE"), ("TAYTAY", "MODERATE")),
            ]
        )
        group_sizes = [1] * 49 + [2] * 72 + [3, 4]
        blocks = []
        source_id = 1
        contributor_base = {"CUBAO": 10, "FAIRVIEW": 20, "TAYTAY": 30}
        for group_index, (options, group_size) in enumerate(
            zip(logical_groups, group_sizes)
        ):
            for row_index in range(group_size):
                campus, difficulty = options[row_index % len(options)]
                contributor_id = contributor_base[campus]
                if 49 <= group_index <= 96:
                    contributor_id += row_index % 2
                vector = (
                    1,
                    *(1 if campus == code else 0 for code in campuses),
                    *(1 if difficulty == code else 0 for code in difficulties),
                    1,
                )
                blocks.append(
                    IdentityBlock(
                        f"question:{source_id}",
                        vector,
                        (
                            IdentityMember(
                                source_id,
                                contributor_id,
                                campus,
                                difficulty,
                                0,
                            ),
                        ),
                        f"logical:{group_index + 1}",
                    )
                )
                source_id += 1
        grouped_blocks = {
            logical_id: [
                block for block in blocks if block.logical_group_id == logical_id
            ]
            for logical_id in {block.logical_group_id for block in blocks}
        }
        self.assertEqual(len(blocks), 200)
        self.assertEqual(len(grouped_blocks), 123)
        self.assertEqual(
            Counter(map(len, grouped_blocks.values())),
            {1: 49, 2: 72, 3: 1, 4: 1},
        )
        self.assertEqual(
            sum(
                len({block.members[0].campus for block in rows}) > 1
                for rows in grouped_blocks.values()
            ),
            2,
        )
        self.assertEqual(
            sum(
                len({block.members[0].contributor_id for block in rows}) > 1
                for rows in grouped_blocks.values()
            ),
            50,
        )
        self.assertFalse(
            any(
                len({block.members[0].difficulty for block in rows}) > 1
                for rows in grouped_blocks.values()
            )
        )

        started = perf_counter()
        result = solve_automatic_identity_aware_two_sets(
            margins=(50, 17, 17, 16, 15, 25, 10, 50),
            blocks=blocks,
            campus_quotas=campus_quotas,
            difficulty_quotas=difficulty_quotas,
            secret=self.secret,
            hmac_context={"input_fingerprint": "s" * 64},
            max_states=1_000_000,
            optimize_soft=True,
        )
        elapsed = perf_counter() - started

        self.assertTrue(result.feasible, result)
        self.assertFalse(result.limit_hit)
        self.assertEqual(result.overlap, 7)
        self.assertLess(result.states_explored, 10_000)
        self.assertLess(elapsed, 30)
        self.assertEqual(len(result.set_a_block_ids), 50)
        self.assertEqual(len(result.set_b_block_ids), 50)
        selected_by_set = (
            set(result.set_a_block_ids),
            set(result.set_b_block_ids),
        )
        blocks_by_id = {block.block_id: block for block in blocks}
        for selected in selected_by_set:
            logical_ids = {
                blocks_by_id[block_id].logical_group_id for block_id in selected
            }
            self.assertEqual(len(logical_ids), 50)
            self.assertEqual(
                Counter(
                    blocks_by_id[block_id].members[0].campus
                    for block_id in selected
                ),
                Counter(campus_quotas),
            )
            self.assertEqual(
                Counter(
                    blocks_by_id[block_id].members[0].difficulty
                    for block_id in selected
                ),
                Counter(difficulty_quotas),
            )
        self.assertEqual(len(selected_by_set[0].intersection(selected_by_set[1])), 7)

    def test_automatic_positive_overlap_is_derived_without_hard_coding(self):
        blocks = (
            IdentityBlock(
                block_id="question:1",
                vector=(1, 1, 0, 1, 1),
                members=(IdentityMember(1, 1, "A", "EASY", 0),),
                logical_group_id="logical:a-1",
            ),
            IdentityBlock(
                block_id="question:2",
                vector=(1, 1, 0, 1, 1),
                members=(IdentityMember(2, 2, "A", "EASY", 0),),
                logical_group_id="logical:a-2",
            ),
            IdentityBlock(
                block_id="question:3",
                vector=(1, 0, 1, 1, 1),
                members=(IdentityMember(3, 3, "B", "EASY", 0),),
                logical_group_id="logical:b",
            ),
        )

        result = solve_automatic_identity_aware_two_sets(
            margins=(2, 1, 1, 2, 2),
            blocks=blocks,
            campus_quotas={"A": 1, "B": 1},
            difficulty_quotas={"EASY": 2},
            secret=self.secret,
            hmac_context={"input_fingerprint": "p" * 64},
            max_states=100,
        )

        self.assertTrue(result.feasible, result)
        self.assertFalse(result.limit_hit)
        self.assertEqual(result.overlap, 1)
        self.assertEqual(len(result.set_a_block_ids), 2)
        self.assertEqual(len(result.set_b_block_ids), 2)
        self.assertEqual(
            len(set(result.set_a_block_ids).intersection(result.set_b_block_ids)),
            1,
        )


class AutomaticProductionShapeOptimizationTests(SimpleTestCase):
    secret = "production-shape-secret"
    campuses = ("C1", "C2", "C3")
    campus_quotas = {"C1": 17, "C2": 17, "C3": 16}
    difficulty_quotas = {"EASY": 15, "MODERATE": 25, "DIFFICULT": 10}

    @staticmethod
    def _review_counterexample_blocks():
        rows = {
            "logical:0": (
                ("C1", "MODERATE", "question:1"),
                ("C1", "EASY", "question:2"),
            ),
            "logical:1": (
                ("C2", "EASY", "question:3"),
                ("C1", "MODERATE", "question:4"),
            ),
            "logical:2": (("C1", "MODERATE", "question:5"),),
            "logical:3": (("C2", "MODERATE", "question:6"),),
            "logical:4": (
                ("C1", "EASY", "question:7"),
                ("C2", "MODERATE", "question:8"),
            ),
            "logical:5": (("C2", "EASY", "question:9"),),
        }
        blocks = []
        source_id = 1
        for contributor_id, (logical_id, alternatives) in enumerate(
            rows.items(), start=1
        ):
            for campus, difficulty, block_id in alternatives:
                blocks.append(
                    IdentityBlock(
                        block_id=block_id,
                        vector=(1,),
                        members=(
                            IdentityMember(
                                source_id,
                                contributor_id,
                                campus,
                                difficulty,
                                0,
                            ),
                        ),
                        logical_group_id=logical_id,
                    )
                )
                source_id += 1
        return tuple(blocks)

    def _assert_review_counterexample_hard_constraints(self, *, result, blocks):
        by_id = {str(block.block_id): block for block in blocks}
        self.assertEqual(result.overlap, 0)
        self.assertEqual(
            set(result.set_a_block_ids).intersection(result.set_b_block_ids), set()
        )
        self.assertEqual(
            {
                by_id[block_id].logical_group_id
                for block_id in result.set_a_block_ids
            }.intersection(
                {
                    by_id[block_id].logical_group_id
                    for block_id in result.set_b_block_ids
                }
            ),
            set(),
        )
        for selected in (result.set_a_block_ids, result.set_b_block_ids):
            self.assertEqual(len(selected), 3)
            self.assertEqual(
                Counter(by_id[block_id].members[0].campus for block_id in selected),
                Counter({"C1": 2, "C2": 1}),
            )
            self.assertEqual(
                len(
                    {
                        by_id[block_id].logical_group_id
                        for block_id in selected
                    }
                ),
                3,
            )

    @staticmethod
    def _exhaustive_review_counterexample_optimum(blocks):
        feasible_sets = []
        target = {"EASY": 2, "MODERATE": 1}
        for selected in combinations(blocks, 3):
            if Counter(
                block.members[0].campus for block in selected
            ) != Counter({"C1": 2, "C2": 1}):
                continue
            logical_ids = {block.logical_group_id for block in selected}
            if len(logical_ids) != 3:
                continue
            counts = Counter(block.members[0].difficulty for block in selected)
            deviation = sum(
                abs(counts[difficulty] - amount)
                for difficulty, amount in target.items()
            )
            feasible_sets.append((logical_ids, deviation))
        return min(
            deviation_a + deviation_b
            for logical_a, deviation_a in feasible_sets
            for logical_b, deviation_b in feasible_sets
            if logical_a.isdisjoint(logical_b)
        )

    def test_review_counterexample_proves_minimum_combined_deviation_two(self):
        blocks = self._review_counterexample_blocks()
        result = solve_automatic_identity_aware_two_sets(
            margins=(3,),
            blocks=blocks,
            campus_quotas={"C1": 2, "C2": 1},
            difficulty_quotas={"EASY": 2, "MODERATE": 1},
            secret="review-counterexample-secret",
            hmac_context={"review_case": "minimum-deviation"},
            max_states=100_000,
            optimize_soft=True,
        )

        self.assertTrue(result.feasible, result)
        self.assertFalse(result.limit_hit)
        self.assertFalse(result.optimization_limit_hit)
        self.assertTrue(result.difficulty_optimality_proved)
        exhaustive_optimum = self._exhaustive_review_counterexample_optimum(blocks)
        self.assertEqual(exhaustive_optimum, 2)
        self.assertEqual(result.difficulty_deviation, exhaustive_optimum)
        self._assert_review_counterexample_hard_constraints(
            result=result, blocks=blocks
        )

    def test_review_counterexample_small_budget_keeps_hard_valid_best_found(self):
        blocks = self._review_counterexample_blocks()
        result = solve_automatic_identity_aware_two_sets(
            margins=(3,),
            blocks=blocks,
            campus_quotas={"C1": 2, "C2": 1},
            difficulty_quotas={"EASY": 2, "MODERATE": 1},
            secret="review-counterexample-secret",
            hmac_context={"review_case": "budget-exhaustion"},
            max_states=1,
            optimize_soft=True,
        )

        self.assertTrue(result.feasible, result)
        self.assertFalse(result.limit_hit)
        self.assertTrue(result.optimization_limit_hit)
        self.assertFalse(result.difficulty_optimality_proved)
        self._assert_review_counterexample_hard_constraints(
            result=result, blocks=blocks
        )

    @staticmethod
    def _target_template():
        cells = (
            ("C1", "EASY", 5),
            ("C1", "MODERATE", 9),
            ("C1", "DIFFICULT", 3),
            ("C2", "EASY", 5),
            ("C2", "MODERATE", 8),
            ("C2", "DIFFICULT", 4),
            ("C3", "EASY", 5),
            ("C3", "MODERATE", 8),
            ("C3", "DIFFICULT", 3),
        )
        return tuple(
            (campus, difficulty)
            for campus, difficulty, count in cells
            for _index in range(count)
        )

    def _shape(self, *, unique_count, submitted_count):
        template = self._target_template()
        rows = []
        for logical_index in range(unique_count):
            campus, difficulty = template[logical_index % len(template)]
            rows.append(
                [
                    campus,
                    difficulty,
                    f"logical:{logical_index}",
                ]
            )
        source_id = 1
        blocks = []
        for campus, difficulty, logical_id in rows:
            blocks.append(
                IdentityBlock(
                    block_id=f"question:{source_id}",
                    vector=(1,),
                    members=(
                        IdentityMember(
                            source_id,
                            (source_id % 7) + 1,
                            campus,
                            difficulty,
                            0,
                        ),
                    ),
                    logical_group_id=logical_id,
                )
            )
            source_id += 1
        for duplicate_index in range(submitted_count - unique_count):
            source = blocks[duplicate_index % unique_count]
            member = source.members[0]
            alternate_campus = self.campuses[
                (self.campuses.index(member.campus) + 1) % len(self.campuses)
            ]
            blocks.append(
                IdentityBlock(
                    block_id=f"question:{source_id}",
                    vector=(1,),
                    members=(
                        IdentityMember(
                            source_id,
                            (source_id % 7) + 1,
                            alternate_campus,
                            member.difficulty,
                            0,
                        ),
                    ),
                    logical_group_id=source.logical_group_id,
                )
            )
            source_id += 1
        return tuple(blocks)

    def _solve_shape(self, *, unique_count, submitted_count):
        blocks = self._shape(
            unique_count=unique_count,
            submitted_count=submitted_count,
        )
        started = perf_counter()
        result = solve_automatic_identity_aware_two_sets(
            margins=(50,),
            blocks=blocks,
            campus_quotas=self.campus_quotas,
            difficulty_quotas=self.difficulty_quotas,
            secret=self.secret,
            hmac_context={"shape": (unique_count, submitted_count)},
            max_states=1_000_000,
        )
        elapsed = perf_counter() - started
        self.assertTrue(result.feasible, result)
        self.assertFalse(result.limit_hit)
        self.assertLess(elapsed, 30)
        by_id = {str(block.block_id): block for block in blocks}
        for selected in (result.set_a_block_ids, result.set_b_block_ids):
            self.assertEqual(len(selected), 50)
            self.assertEqual(
                Counter(by_id[block_id].members[0].campus for block_id in selected),
                Counter(self.campus_quotas),
            )
            self.assertEqual(
                len({by_id[block_id].logical_group_id for block_id in selected}),
                50,
            )
        return result, blocks, elapsed

    def test_fm322_like_150_submitted_102_unique_completes_quickly(self):
        result, _blocks, elapsed = self._solve_shape(
            unique_count=102,
            submitted_count=150,
        )
        self.assertEqual(result.overlap, 0)
        self.assertTrue(result.difficulty_target_met)
        print(f"AUTOMATIC_SHAPE FM322 elapsed={elapsed:.6f}s")

    def test_ge213_like_150_submitted_98_unique_uses_two_overlap(self):
        result, _blocks, elapsed = self._solve_shape(
            unique_count=98,
            submitted_count=150,
        )
        self.assertEqual(result.overlap, 2)
        print(f"AUTOMATIC_SHAPE GE213 elapsed={elapsed:.6f}s")

    def test_fm311_like_150_submitted_101_unique_completes_quickly(self):
        result, _blocks, elapsed = self._solve_shape(
            unique_count=101,
            submitted_count=150,
        )
        self.assertEqual(result.overlap, 0)
        self.assertTrue(result.difficulty_target_met)
        print(f"AUTOMATIC_SHAPE FM311 elapsed={elapsed:.6f}s")

    def test_is313_like_impossible_target_uses_proved_closest_mix(self):
        mix = (
            (("C1", "EASY"), 11),
            (("C1", "MODERATE"), 12),
            (("C1", "DIFFICULT"), 2),
            (("C2", "EASY"), 11),
            (("C2", "MODERATE"), 11),
            (("C2", "DIFFICULT"), 3),
        )
        one_set = tuple(cell for cell, count in mix for _index in range(count))
        blocks = tuple(
            IdentityBlock(
                block_id=f"question:{index + 1}",
                vector=(1,),
                members=(
                    IdentityMember(
                        index + 1,
                        (index % 5) + 1,
                        campus,
                        difficulty,
                        0,
                    ),
                ),
                logical_group_id=f"logical:{index + 1}",
            )
            for index, (campus, difficulty) in enumerate(one_set + one_set)
        )
        started = perf_counter()
        result = solve_automatic_identity_aware_two_sets(
            margins=(50,),
            blocks=blocks,
            campus_quotas={"C1": 25, "C2": 25},
            difficulty_quotas=self.difficulty_quotas,
            secret=self.secret,
            hmac_context={"shape": "is313"},
            max_states=1_000_000,
        )
        elapsed = perf_counter() - started
        self.assertTrue(result.feasible, result)
        self.assertEqual(result.overlap, 0)
        self.assertFalse(result.difficulty_target_met)
        self.assertTrue(result.difficulty_optimality_proved)
        self.assertEqual(result.difficulty_deviation, 28)
        by_id = {str(block.block_id): block for block in blocks}
        combined = Counter(
            by_id[block_id].members[0].difficulty
            for selected in (result.set_a_block_ids, result.set_b_block_ids)
            for block_id in selected
        )
        self.assertEqual(
            combined,
            Counter({"EASY": 44, "MODERATE": 46, "DIFFICULT": 10}),
        )
        repeated = solve_automatic_identity_aware_two_sets(
            margins=(50,),
            blocks=blocks,
            campus_quotas={"C1": 25, "C2": 25},
            difficulty_quotas=self.difficulty_quotas,
            secret=self.secret,
            hmac_context={"shape": "is313"},
            max_states=1_000_000,
        )
        self.assertEqual(repeated.set_a_block_ids, result.set_a_block_ids)
        self.assertEqual(repeated.set_b_block_ids, result.set_b_block_ids)
        self.assertLess(elapsed, 30)
        print(f"AUTOMATIC_SHAPE IS313 elapsed={elapsed:.6f}s")

    def test_definitive_fast_path_infeasibility_does_not_use_generic_recursion(self):
        blocks = tuple(
            IdentityBlock(
                block_id=f"question:{index}",
                vector=(1,),
                members=(IdentityMember(index, index, "C1", "EASY", 0),),
                logical_group_id=f"logical:{index}",
            )
            for index in range(1, 4)
        )
        with patch(
            "apps.departmental_exams.generation_algorithms.solve_identity_aware_two_sets",
            side_effect=AssertionError("generic recursion must not run"),
        ) as generic:
            result = solve_automatic_identity_aware_two_sets(
                margins=(2,),
                blocks=blocks,
                campus_quotas={"C1": 1, "C2": 1},
                difficulty_quotas={"EASY": 2},
                secret=self.secret,
                hmac_context={"shape": "infeasible"},
                max_states=100,
            )
        self.assertFalse(result.feasible)
        self.assertFalse(result.limit_hit)
        generic.assert_not_called()

    def test_budget_exhaustion_is_distinct_from_proved_infeasibility(self):
        result = solve_automatic_identity_aware_two_sets(
            margins=(1,),
            blocks=(
                IdentityBlock(
                    block_id="question:1",
                    vector=(1,),
                    members=(IdentityMember(1, 1, "C1", "EASY", 0),),
                    logical_group_id="logical:1",
                ),
            ),
            campus_quotas={"C1": 1},
            difficulty_quotas={"EASY": 1},
            secret=self.secret,
            hmac_context={"shape": "budget"},
            max_states=0,
        )
        self.assertFalse(result.feasible)
        self.assertTrue(result.limit_hit)

    def test_automatic_impossible_positive_overlap_stays_infeasible(self):
        blocks = tuple(
            IdentityBlock(
                block_id=f"question:{source_id}",
                vector=(1, 1, 1, 1),
                members=(IdentityMember(source_id, source_id, "A", "EASY", 0),),
                logical_group_id="only-logical-question",
            )
            for source_id in (1, 2)
        )

        result = solve_automatic_identity_aware_two_sets(
            margins=(2, 2, 2, 2),
            blocks=blocks,
            campus_quotas={"A": 2},
            difficulty_quotas={"EASY": 2},
            secret=self.secret,
            hmac_context={"input_fingerprint": "i" * 64},
            max_states=100,
        )

        self.assertFalse(result.feasible)
        self.assertFalse(result.limit_hit)

    def test_automatic_soft_contributor_limit_keeps_hard_feasible_selection(self):
        blocks = tuple(
            IdentityBlock(
                block_id=f"question:{source_id}",
                vector=(1, 1, 1, 1),
                members=(
                    IdentityMember(
                        source_id,
                        source_id,
                        "CUBAO",
                        "EASY",
                        0,
                    ),
                ),
                logical_group_id=f"logical:{source_id}",
            )
            for source_id in range(1, 5)
        )
        result = solve_automatic_identity_aware_two_sets(
            margins=(2, 2, 2, 2),
            blocks=blocks,
            campus_quotas={"CUBAO": 2},
            difficulty_quotas={"EASY": 2},
            secret=self.secret,
            hmac_context={"input_fingerprint": "f" * 64},
            max_states=2,
            optimize_soft=True,
        )

        self.assertTrue(result.feasible, result)
        self.assertFalse(result.limit_hit)
        self.assertEqual(result.overlap, 0)
        self.assertEqual(len(result.set_a_block_ids), 2)
        self.assertEqual(len(result.set_b_block_ids), 2)
