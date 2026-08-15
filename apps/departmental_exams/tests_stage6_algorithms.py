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
        per_campus = ["EASY"] * 15 + ["MODERATE"] * 25 + ["DIFFICULT"] * 10
        rows = []
        source_id = 1
        for contributor_id, campus in enumerate(campuses, start=1):
            for difficulty in per_campus:
                rows.append((source_id, contributor_id, campus, difficulty))
                source_id += 1
        for _source, contributor_id, campus, difficulty in rows[:50]:
            rows.append((source_id, contributor_id, campus, difficulty))
            source_id += 1

        required = {
            "CUBAO": {"EASY": 10, "MODERATE": 18, "DIFFICULT": 6},
            "FAIRVIEW": {"EASY": 10, "MODERATE": 18, "DIFFICULT": 6},
            "TAYTAY": {"EASY": 10, "MODERATE": 14, "DIFFICULT": 8},
        }
        singleton_ids = []
        for campus, counts in required.items():
            for difficulty, count in counts.items():
                singleton_ids.extend(
                    [
                        row[0]
                        for row in rows
                        if row[2] == campus and row[3] == difficulty
                    ][:count]
                )
        singleton_ids.extend(row[0] for row in rows if row[0] not in singleton_ids)
        singleton_ids = set(singleton_ids[:121])
        duplicate_ids = [row[0] for row in rows if row[0] not in singleton_ids]
        alpha_ids = set(duplicate_ids[:39])
        blocks = []
        for row in rows:
            source_id, contributor_id, campus, difficulty = row
            logical_group_id = (
                f"singleton:{source_id}"
                if source_id in singleton_ids
                else "alternative:alpha"
                if source_id in alpha_ids
                else "alternative:beta"
            )
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
                    logical_group_id,
                )
            )
        result = solve_automatic_identity_aware_two_sets(
            margins=(50, 17, 17, 16, 15, 25, 10, 50),
            blocks=blocks,
            campus_quotas=campus_quotas,
            difficulty_quotas=difficulty_quotas,
            secret=self.secret,
            hmac_context={"input_fingerprint": "s" * 64},
            max_states=250_000,
            optimize_soft=True,
        )

        self.assertTrue(result.feasible, result)
        self.assertFalse(result.limit_hit)
        self.assertEqual(result.overlap, 0)
        self.assertLess(result.states_explored, 250_000)
        self.assertEqual(len(result.set_a_block_ids), 50)
        self.assertEqual(len(result.set_b_block_ids), 50)
        selected_by_set = (
            set(result.set_a_block_ids),
            set(result.set_b_block_ids),
        )
        for selected in selected_by_set:
            logical_ids = {
                next(
                    block.logical_group_id
                    for block in blocks
                    if block.block_id == block_id
                )
                for block_id in selected
            }
            self.assertEqual(len(logical_ids), 50)

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
