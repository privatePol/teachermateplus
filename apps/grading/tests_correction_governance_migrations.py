"""Disposable-database regressions for correction-governance migration 0034."""

from importlib import import_module
from types import SimpleNamespace

from django.db import IntegrityError, connection, transaction
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase


migration_0034 = import_module("apps.grading.migrations.0034_correctionpetitionwindowpolicy_canonical_period")
migration_0035 = import_module("apps.grading.migrations.0035_gradecorrectionapprovalauthoritysnapshot")


class CorrectionGovernanceMigrationTests(TransactionTestCase):
    migrate_from = ("grading", "0033_alter_coursetemplateassignment_grading_template")
    migrate_to = ("grading", "0034_correctionpetitionwindowpolicy_canonical_period")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.from_state = self._state(self.migrate_from)
        self.to_state = self._state(self.migrate_to)
        # The Django test runner begins at every leaf migration.  Migration
        # 0034 is intentionally irreversible, so rebuild just this isolated
        # policy table at its 0033 state instead of attempting a forbidden
        # global reverse.  The subsequent executor call is a real forward
        # 0033 -> 0034 migration on the disposable test database.
        current_apps = self.executor.loader.project_state(self.to_state).apps
        current_policy = current_apps.get_model("grading", "CorrectionPetitionWindowPolicy")
        old_policy = self.executor.loader.project_state(self.from_state).apps.get_model(
            "grading", "CorrectionPetitionWindowPolicy"
        )
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(current_policy)
            schema_editor.create_model(old_policy)
        MigrationRecorder(connection).migration_qs.filter(
            app="grading", name=self.migrate_to[1]
        ).delete()
        self.executor = MigrationExecutor(connection)
        self.old_apps = self.executor.loader.project_state(self.from_state).apps
        self.fixture = self._legacy_fixture()

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def _state(self, grading_target):
        return [
            grading_target if app_label == "grading" else node
            for node in self.executor.loader.graph.leaf_nodes()
            for app_label, _name in [node]
        ]

    def _legacy_fixture(self):
        Tenant = self.old_apps.get_model("tenants", "Tenant")
        Campus = self.old_apps.get_model("tenants", "Campus")
        AcademicYear = self.old_apps.get_model("academics", "AcademicYear")
        Term = self.old_apps.get_model("academics", "Term")
        Template = self.old_apps.get_model("grading", "GradingTemplate")
        Period = self.old_apps.get_model("grading", "GradingTemplatePeriod")
        Policy = self.old_apps.get_model("grading", "CorrectionPetitionWindowPolicy")
        suffix = self._testMethodName[-18:].upper()
        tenant = Tenant.objects.create(code=f"M{suffix}", name=suffix)
        campus = Campus.objects.create(tenant=tenant, code=suffix, name=suffix)
        academic_year = AcademicYear.objects.create(
            tenant=tenant,
            code=suffix,
            name=suffix,
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        term = Term.objects.create(tenant=tenant, academic_year=academic_year, code=suffix, name=suffix)
        template = Template.objects.create(tenant=tenant, code=suffix, name=suffix, is_published=True)
        period = Period.objects.create(template=template, code="PRELIM", name="Prelim", sequence_no=1)
        policy = Policy.objects.create(
            tenant=tenant,
            campus=campus,
            academic_year=academic_year,
            term=term,
            grading_period=period,
            policy_mode="OPEN_ANYTIME",
            is_active=True,
        )
        return {
            "tenant": tenant,
            "campus": campus,
            "academic_year": academic_year,
            "term": term,
            "template": template,
            "period": period,
            "policy": policy,
        }

    def _old_period(self, *, code, name):
        Period = self.old_apps.get_model("grading", "GradingTemplatePeriod")
        return Period.objects.create(
            template_id=self.fixture["template"].id,
            code=code,
            name=name,
            sequence_no=2,
        )

    def _old_policy(self, *, period, is_active=True):
        Policy = self.old_apps.get_model("grading", "CorrectionPetitionWindowPolicy")
        return Policy.objects.create(
            tenant_id=self.fixture["tenant"].id,
            campus_id=self.fixture["campus"].id,
            academic_year_id=self.fixture["academic_year"].id,
            term_id=self.fixture["term"].id,
            grading_period_id=period.id,
            policy_mode="OPEN_ANYTIME",
            is_active=is_active,
        )

    def _forward(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.to_state)
        return self.executor.loader.project_state(self.to_state).apps

    def test_clean_0033_to_0034_forward_backfills_code_identity_and_digest(self):
        apps = self._forward()
        Policy = apps.get_model("grading", "CorrectionPetitionWindowPolicy")
        policy = Policy.objects.get(pk=self.fixture["policy"].id)

        self.assertEqual(policy.canonical_period_key, "PRELIM")
        self.assertEqual(len(policy.active_scope_key), 64)
        self.assertEqual(Policy._meta.get_field("canonical_period_key").max_length, 120)
        self.assertEqual(Policy._meta.get_field("active_scope_key").max_length, 64)

    def test_name_fallback_at_maximum_length_is_stored_without_truncation(self):
        longest_name = "N" * 120
        legacy_policy = self._old_policy(period=self._old_period(code="", name=longest_name))

        apps = self._forward()
        Policy = apps.get_model("grading", "CorrectionPetitionWindowPolicy")
        migrated = Policy.objects.get(pk=legacy_policy.id)

        self.assertEqual(migrated.canonical_period_key, longest_name)
        self.assertEqual(len(migrated.canonical_period_key), 120)
        self.assertEqual(len(migrated.active_scope_key), 64)

    def test_active_scope_key_generation_is_deterministic_and_fixed_length(self):
        policy = SimpleNamespace(
            tenant_id=1,
            campus_id=None,
            academic_year_id=2,
            term_id=3,
            is_active=True,
        )

        first = migration_0034._active_scope_key(policy, "CUSTOMPERIOD")
        second = migration_0034._active_scope_key(policy, "CUSTOMPERIOD")

        self.assertEqual(first, second)
        self.assertEqual(len(first), migration_0034.ACTIVE_SCOPE_KEY_MAX_LENGTH)

    def test_equivalent_canonical_logical_scopes_produce_same_active_key(self):
        policy = SimpleNamespace(
            tenant_id=10,
            campus_id=20,
            academic_year_id=30,
            term_id=40,
            is_active=True,
        )
        pre_final = migration_0034._canonical_period_key("PRE-FINAL")
        prefinal = migration_0034._canonical_period_key("PREFINAL")

        self.assertEqual(pre_final, prefinal)
        self.assertEqual(
            migration_0034._active_scope_key(policy, pre_final),
            migration_0034._active_scope_key(policy, prefinal),
        )

    def test_distinct_logical_scopes_produce_distinct_active_keys(self):
        base = SimpleNamespace(
            tenant_id=10,
            campus_id=20,
            academic_year_id=30,
            term_id=40,
            is_active=True,
        )
        broad = SimpleNamespace(
            tenant_id=10,
            campus_id=None,
            academic_year_id=30,
            term_id=40,
            is_active=True,
        )

        self.assertNotEqual(
            migration_0034._active_scope_key(base, "CUSTOMPERIOD"),
            migration_0034._active_scope_key(broad, "CUSTOMPERIOD"),
        )
        self.assertNotEqual(
            migration_0034._active_scope_key(base, "CUSTOMPERIOD"),
            migration_0034._active_scope_key(base, "ANOTHERPERIOD"),
        )

    def test_canonical_collision_is_rejected_before_ddl(self):
        self._old_policy(period=self._old_period(code="PRE-FINAL", name="Pre Final"))
        self._old_policy(period=self._old_period(code="PREFINAL", name="Pre Final Duplicate"))

        with self.assertRaisesRegex(RuntimeError, "collapse to the same canonical scope"):
            self._forward()
        columns = {column.name for column in connection.introspection.get_table_description(
            connection.cursor(), "correction_petition_window_policies"
        )}
        self.assertNotIn("canonical_period_key", columns)
        self.assertNotIn("active_scope_key", columns)
        self.old_apps.get_model("grading", "CorrectionPetitionWindowPolicy").objects.exclude(
            pk=self.fixture["policy"].id
        ).delete()

    def test_empty_legacy_period_identity_is_rejected_before_ddl(self):
        self._old_policy(period=self._old_period(code="", name=""))

        with self.assertRaisesRegex(RuntimeError, "no usable grading-period identity"):
            self._forward()
        self.old_apps.get_model("grading", "CorrectionPetitionWindowPolicy").objects.exclude(
            pk=self.fixture["policy"].id
        ).delete()

    def test_overlong_legacy_canonical_identity_is_rejected_before_ddl(self):
        self._old_policy(period=self._old_period(code="", name="X" * 121))

        with self.assertRaisesRegex(RuntimeError, "exceeds 120 characters"):
            self._forward()
        columns = {column.name for column in connection.introspection.get_table_description(
            connection.cursor(), "correction_petition_window_policies"
        )}
        self.assertNotIn("canonical_period_key", columns)
        self.assertNotIn("active_scope_key", columns)
        self.old_apps.get_model("grading", "CorrectionPetitionWindowPolicy").objects.exclude(
            pk=self.fixture["policy"].id
        ).delete()

    def test_active_duplicate_is_rejected_by_nullable_unique_digest(self):
        self._old_policy(period=self._old_period(code="PRE-FINAL", name="Pre Final"), is_active=False)
        apps = self._forward()
        Policy = apps.get_model("grading", "CorrectionPetitionWindowPolicy")
        inactive = Policy.objects.filter(is_active=False).get()
        active = Policy.objects.get(pk=self.fixture["policy"].id)

        self.assertIsNone(inactive.active_scope_key)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Policy.objects.create(
                tenant_id=active.tenant_id,
                campus_id=active.campus_id,
                academic_year_id=active.academic_year_id,
                term_id=active.term_id,
                grading_period_id=active.grading_period_id,
                canonical_period_key=active.canonical_period_key,
                active_scope_key=active.active_scope_key,
                policy_mode="CLOSED",
                is_active=True,
            )

    def test_multiple_inactive_duplicate_history_rows_are_allowed_as_null(self):
        first = self._old_policy(period=self._old_period(code="PRE-FINAL", name="Pre Final"), is_active=False)
        second = self._old_policy(period=self._old_period(code="PREFINAL", name="Pre Final Alias"), is_active=False)

        apps = self._forward()
        Policy = apps.get_model("grading", "CorrectionPetitionWindowPolicy")
        migrated = list(Policy.objects.filter(pk__in=[first.id, second.id]).order_by("id"))

        self.assertEqual([row.canonical_period_key for row in migrated], ["PREFINAL", "PREFINAL"])
        self.assertEqual([row.active_scope_key for row in migrated], [None, None])

    def test_pre_final_and_prefinal_normalize_to_same_canonical_identity(self):
        self.assertEqual(migration_0034._canonical_period_key("PRE-FINAL"), "PREFINAL")
        self.assertEqual(migration_0034._canonical_period_key("PREFINAL"), "PREFINAL")
        self.assertEqual(migration_0034._canonical_period_key("Pre Final"), "PREFINAL")

    def test_custom_periods_remain_distinct_from_standard_periods(self):
        canonical = migration_0034._canonical_period_key
        self.assertEqual(canonical("PRELIM"), "PRELIM")
        self.assertEqual(canonical("MIDTERM"), "MIDTERM")
        self.assertEqual(canonical("FINAL"), "FINAL")
        self.assertEqual(canonical("MIDTERM-REMEDIAL"), "MIDTERMREMEDIAL")
        self.assertEqual(canonical("PRELIMINARY"), "PRELIMINARY")
        self.assertEqual(canonical("POST-FINAL"), "POSTFINAL")
        self.assertEqual(canonical("PREFI-SPECIAL"), "PREFISPECIAL")
        self.assertEqual(canonical("FINAL-RETAKE"), "FINALRETAKE")

        policy = SimpleNamespace(
            tenant_id=10,
            campus_id=20,
            academic_year_id=30,
            term_id=40,
            is_active=True,
        )
        self.assertNotEqual(
            migration_0034._active_scope_key(policy, canonical("MIDTERM-REMEDIAL")),
            migration_0034._active_scope_key(policy, canonical("MIDTERM")),
        )

    def test_exact_alias_and_custom_identity_normalization_matrix(self):
        expected = {
            "PRELIM": "PRELIM",
            "MIDTERM": "MIDTERM",
            "PRE-FINAL": "PREFINAL",
            "PRE FINAL": "PREFINAL",
            "PREFINAL": "PREFINAL",
            "FINAL": "FINAL",
            "MIDTERM-REMEDIAL": "MIDTERMREMEDIAL",
            "PRELIMINARY": "PRELIMINARY",
            "POST-FINAL": "POSTFINAL",
            "PREFI-SPECIAL": "PREFISPECIAL",
            "FINAL-RETAKE": "FINALRETAKE",
            "CUSTOM PERIOD": "CUSTOMPERIOD",
        }
        for value, canonical_key in expected.items():
            self.assertEqual(migration_0034._canonical_period_key(value), canonical_key)

    def test_reverse_is_intentionally_refused_before_reverse_ddl(self):
        self._forward()
        with self.assertRaises(IrreversibleError):
            MigrationExecutor(connection).migrate(self.from_state)


class CorrectionApprovalAuthoritySnapshotMigrationTests(TransactionTestCase):
    migrate_from = ("grading", "0034_correctionpetitionwindowpolicy_canonical_period")
    migrate_to = ("grading", "0035_gradecorrectionapprovalauthoritysnapshot")

    def _state(self, grading_target):
        executor = MigrationExecutor(connection)
        return [
            grading_target if app_label == "grading" else node
            for node in executor.loader.graph.leaf_nodes()
            for app_label, _name in [node]
        ]

    def test_0035_creates_append_only_snapshot_schema_without_legacy_backfill(self):
        from_state = self._state(self.migrate_from)
        to_state = self._state(self.migrate_to)
        executor = MigrationExecutor(connection)
        executor.migrate(from_state)
        executor = MigrationExecutor(connection)
        executor.migrate(to_state)
        apps = executor.loader.project_state(to_state).apps
        Snapshot = apps.get_model("grading", "GradeCorrectionApprovalAuthoritySnapshot")

        self.assertEqual(Snapshot._meta.db_table, "grade_correction_approval_authority_snapshots")
        self.assertTrue(Snapshot._meta.get_field("approval_step").unique)
        self.assertTrue(Snapshot._meta.get_field("approval_audit_log").unique)
        self.assertTrue(Snapshot._meta.get_field("authority_assignment").null)
        self.assertTrue(Snapshot._meta.get_field("authority_assignment_assigned_at").null)
        self.assertEqual(Snapshot.objects.count(), 0)
        self.assertFalse(any(operation.__class__.__name__ == "RunPython" for operation in migration_0035.Migration.operations))
