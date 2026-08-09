from django.test import SimpleTestCase

from .generation_algorithms import (
    AllocationError,
    allocate_campuses,
    allocate_difficulties,
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
