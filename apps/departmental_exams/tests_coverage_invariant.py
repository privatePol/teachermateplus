from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import CourseExamConfiguration
from .services import (
    CourseExamConfigurationService,
    ExaminationCycleConfigurationService,
)
from .stage4_test_support import Stage4TestCase


class CoverageInvariantTests(Stage4TestCase):
    def make_configurable_course(self, *, default_coverage="Cycle coverage"):
        scope_index = getattr(self, "_coverage_scope_index", 0) + 1
        self._coverage_scope_index = scope_index
        cycle = self.make_cycle(
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            default_coverage=default_coverage,
            scope_suffix=f"COV{scope_index}",
        )
        return cycle, self.make_course(cycle=cycle)

    def save_coverage(self, *, parent, coverage, coverage_mode, configuration=None):
        return CourseExamConfigurationService.save_course_draft(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision if configuration else 0,
            final_item_count=50,
            questions_required_per_faculty=50,
            final_item_count_mode="DEFAULT",
            questions_required_per_faculty_mode="DEFAULT",
            coverage=coverage,
            coverage_mode=coverage_mode,
            additional_instructions="",
            contribution_deadline=parent.cycle.default_contribution_deadline,
            contribution_deadline_mode="DEFAULT",
        )

    def test_model_accepts_only_the_three_coverage_states(self):
        cases = (
            ("", None),
            ("Cycle coverage", CourseExamConfiguration.ValueSource.DEFAULT),
            ("Course coverage", CourseExamConfiguration.ValueSource.OVERRIDE),
        )
        for index, (coverage, source) in enumerate(cases):
            with self.subTest(coverage=coverage, source=source):
                _cycle, parent = self.make_configurable_course(
                    default_coverage=f"Default {index}"
                )
                configuration = self.make_configuration(
                    parent,
                    coverage=coverage,
                    coverage_source=source,
                )
                configuration.full_clean()

        _cycle, parent = self.make_configurable_course(default_coverage="Invalid")
        configuration = self.make_configuration(
            parent,
            coverage="Valid first",
            coverage_source=CourseExamConfiguration.ValueSource.OVERRIDE,
        )
        for coverage, source in (
            ("", CourseExamConfiguration.ValueSource.DEFAULT),
            ("", CourseExamConfiguration.ValueSource.OVERRIDE),
            ("Nonblank", None),
            ("Nonblank", "INVALID"),
        ):
            with self.subTest(invalid_coverage=coverage, invalid_source=source):
                configuration.coverage = coverage
                configuration.coverage_source = source
                with self.assertRaises(ValidationError):
                    configuration.full_clean()

    def test_database_constraint_accepts_valid_and_rejects_invalid_states(self):
        valid = []
        for index, (coverage, source) in enumerate(
            (
                ("", None),
                ("Default coverage", CourseExamConfiguration.ValueSource.DEFAULT),
                ("Override coverage", CourseExamConfiguration.ValueSource.OVERRIDE),
            )
        ):
            _cycle, parent = self.make_configurable_course(
                default_coverage=f"Constraint {index}"
            )
            valid.append(
                self.make_configuration(
                    parent,
                    coverage=coverage,
                    coverage_source=source,
                )
            )

        blank, default, override = valid
        for configuration, values in (
            (blank, {"coverage_source": "DEFAULT"}),
            (blank, {"coverage_source": "OVERRIDE"}),
            (default, {"coverage_source": None}),
            (override, {"coverage_source": "INVALID"}),
        ):
            with self.subTest(configuration=configuration.id, values=values):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    CourseExamConfiguration.objects.filter(
                        pk=configuration.pk
                    ).update(**values)

    def test_service_normalizes_default_override_blank_and_legacy_posts(self):
        cycle, parent = self.make_configurable_course(
            default_coverage="Approved cycle coverage"
        )
        configuration, _changed = self.save_coverage(
            parent=parent,
            coverage="ignored for default",
            coverage_mode="DEFAULT",
        )
        self.assertEqual(
            (configuration.coverage, configuration.coverage_source),
            ("Approved cycle coverage", "DEFAULT"),
        )

        configuration, _changed = self.save_coverage(
            parent=parent,
            coverage="Explicit course coverage",
            coverage_mode="OVERRIDE",
            configuration=configuration,
        )
        self.assertEqual(
            (configuration.coverage, configuration.coverage_source),
            ("Explicit course coverage", "OVERRIDE"),
        )

        configuration, _changed = self.save_coverage(
            parent=parent,
            coverage="   ",
            coverage_mode="OVERRIDE",
            configuration=configuration,
        )
        self.assertEqual((configuration.coverage, configuration.coverage_source), ("", None))

        configuration, _changed = self.save_coverage(
            parent=parent,
            coverage="ignored while returning to default",
            coverage_mode="DEFAULT",
            configuration=configuration,
        )
        self.assertEqual(
            (configuration.coverage, configuration.coverage_source),
            (cycle.default_coverage, "DEFAULT"),
        )

        configuration, _changed = self.save_coverage(
            parent=parent,
            coverage=configuration.coverage,
            coverage_mode=None,
            configuration=configuration,
        )
        self.assertEqual(configuration.coverage_source, "DEFAULT")
        configuration, _changed = self.save_coverage(
            parent=parent,
            coverage="Legacy changed coverage",
            coverage_mode=None,
            configuration=configuration,
        )
        self.assertEqual(
            (configuration.coverage, configuration.coverage_source),
            ("Legacy changed coverage", "OVERRIDE"),
        )
        configuration, _changed = self.save_coverage(
            parent=parent,
            coverage="",
            coverage_mode=None,
            configuration=configuration,
        )
        self.assertEqual((configuration.coverage, configuration.coverage_source), ("", None))

    def test_blank_default_and_later_apply_defaults_populate_unconfigured_course(self):
        cycle, parent = self.make_configurable_course(default_coverage="")
        configuration, _changed = self.save_coverage(
            parent=parent,
            coverage="",
            coverage_mode="DEFAULT",
        )
        self.assertEqual((configuration.coverage, configuration.coverage_source), ("", None))

        ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.manager,
            expected_updated_at=(
                ExaminationCycleConfigurationService.transition_token(cycle)
            ),
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=cycle.default_contribution_deadline,
            default_coverage="Later approved coverage",
            contributor_instructions=cycle.contributor_instructions,
            processing_mode=cycle.processing_mode,
        )
        configuration.refresh_from_db()
        self.assertEqual(
            (configuration.coverage, configuration.coverage_source),
            ("Later approved coverage", "DEFAULT"),
        )

    def test_apply_defaults_preserves_opened_history_bearing_configuration(self):
        cycle, parent = self.make_configurable_course(default_coverage="Old default")
        configuration = self.make_configuration(
            parent,
            workflow=CourseExamConfiguration.WorkflowStatus.CLOSED,
            opened_at=self.future_deadline(),
            coverage="Historical course coverage",
            coverage_source=CourseExamConfiguration.ValueSource.OVERRIDE,
        )
        original_revision = configuration.revision
        ExaminationCycleConfigurationService.save_cycle_configuration(
            cycle_id=cycle.id,
            tenant_id=self.tenant.id,
            user=self.manager,
            expected_updated_at=(
                ExaminationCycleConfigurationService.transition_token(cycle)
            ),
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=cycle.default_contribution_deadline,
            default_coverage="New default",
            contributor_instructions=cycle.contributor_instructions,
            processing_mode=cycle.processing_mode,
        )
        configuration.refresh_from_db()
        self.assertEqual(
            (configuration.coverage, configuration.coverage_source),
            ("Historical course coverage", "OVERRIDE"),
        )
        self.assertEqual(configuration.revision, original_revision)
