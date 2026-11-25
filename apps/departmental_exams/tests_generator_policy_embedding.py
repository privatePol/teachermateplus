"""Cycle-page form isolation and existing generator lifecycle contract."""
from html.parser import HTMLParser

from apps.rbac.models import Permission, UserPermission
from django.urls import reverse

from .models import ExamGeneratorPolicy
from .services import ExaminationCycleConfigurationService
from .stage4_test_support import Stage4TestCase


class GeneratorPolicyEmbeddingTests(Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def policy_data(self, revision=0):
        return {"expected_revision": revision, "departmental": "on", "standardized": "on",
                "mode": "SHUFFLED_ORDER", "shuffle_choices": "on"}

    def test_existing_lock_opens_only_at_first_course_contribution(self):
        cycle = self.make_cycle(default_questions_required_per_faculty=50, default_final_item_count=50)
        parent = self.make_course(cycle=cycle)
        configuration = self.make_configuration(parent)
        url = reverse("departmental_exams:generator_policy", args=[cycle.id])
        self.assertEqual(self.client.post(url, self.policy_data()).status_code, 302)
        response = self.client.post(reverse("departmental_exams:cycle_open", args=[cycle.id]), {
            "expected_updated_at": ExaminationCycleConfigurationService.transition_token(cycle)})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.client.get(url).context["locked"])
        cycle_url = reverse("departmental_exams:cycle_configuration", args=[cycle.id])
        self.assertFalse(self.client.get(cycle_url).context["generator_locked"])
        self.assertEqual(self.client.post(url, self.policy_data(1)).status_code, 302)
        response = self.client.post(reverse("departmental_exams:course_contribution_open", args=[parent.id]),
                                    {"expected_revision": configuration.revision})
        self.assertEqual(response.status_code, 302)
        configuration.refresh_from_db()
        self.assertIsNotNone(configuration.opened_at)
        locked = self.client.get(url)
        self.assertTrue(locked.context["locked"])
        self.assertContains(locked, "<fieldset disabled")
        before = ExamGeneratorPolicy.objects.values().get(cycle=cycle)
        data = self.policy_data(2)
        data["mode"] = "DIFFERENT"
        self.assertEqual(self.client.post(url, data).status_code, 409)
        self.assertEqual(ExamGeneratorPolicy.objects.values().get(cycle=cycle), before)
        embedded = self.client.get(cycle_url)
        self.assertTrue(embedded.context["generator_locked"])
        self.assertContains(embedded, "This policy is frozen")
        self.assertContains(embedded, "<fieldset disabled")
        data["action"] = "save_generator_policy"
        rejected = self.client.post(cycle_url, data)
        self.assertContains(rejected, "Generator policy freezes at first contribution Open", status_code=409)
        self.assertEqual(ExamGeneratorPolicy.objects.values().get(cycle=cycle), before)

    def cycle_url(self, cycle):
        return reverse("departmental_exams:cycle_configuration", args=[cycle.id])

    def defaults_data(self, cycle):
        return {"expected_updated_at": ExaminationCycleConfigurationService.transition_token(cycle),
                "default_questions_required_per_faculty": 60, "default_final_item_count": 55,
                "default_coverage": "Updated coverage", "contributor_instructions": "Instructions", "reason": ""}

    def test_rendered_card_position_responsive_groups_and_separate_forms(self):
        class Forms(HTMLParser):
            def __init__(self):
                super().__init__()
                self.forms, self.current = [], None
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "form":
                    assert self.current is None, "Forms must not nest"
                    self.current = []
                    self.forms.append(self.current)
                elif self.current is not None and tag in ("input", "select", "textarea"):
                    self.current.append(attrs.get("name"))
            def handle_endtag(self, tag):
                if tag == "form":
                    self.current = None
        cycle = self.make_cycle()
        response = self.client.get(self.cycle_url(cycle))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertLess(html.index('id="automatic-generation-heading"'), html.index('id="question-requirements-heading"'))
        self.assertLess(html.index('>Exam Generator Config</div>'), html.index('>Lifecycle Actions</div>'))
        self.assertNotIn(reverse("departmental_exams:generator_policy", args=[cycle.id]), html)
        for name in ("automatic_campus_contribution_policy", "automatic_contributor_completion_policy",
                     "default_questions_required_per_faculty", "default_final_item_count"):
            self.assertIn('<div class="col-md-6"><label class="form-label" for="id_' + name, html)
        forms = Forms()
        forms.feed(html)
        defaults = next(row for row in forms.forms if "expected_updated_at" in row)
        policy = next(row for row in forms.forms if "expected_revision" in row)
        self.assertNotIn("mode", defaults)
        self.assertNotIn("default_final_item_count", policy)
        self.assertIn("Save Generator Policy", html)
        self.assertIn("Settings lock when contributions first open.", html)

    def test_policy_save_changes_only_policy_and_defaults_save_only_defaults(self):
        cycle = self.make_cycle()
        url = self.cycle_url(cycle)
        before_cycle = type(cycle).objects.values().get(pk=cycle.pk)
        data = self.policy_data()
        data.update(self.defaults_data(cycle))
        data["action"] = "save_generator_policy"
        response = self.client.post(url, data)
        self.assertRedirects(response, url)
        self.assertEqual(type(cycle).objects.values().get(pk=cycle.pk), before_cycle)
        before_policy = ExamGeneratorPolicy.objects.values().get(cycle=cycle)
        defaults = self.defaults_data(cycle)
        defaults.update({"mode": "DIFFERENT", "expected_revision": 999})
        confirmation = self.client.post(url, defaults)
        self.assertTemplateUsed(confirmation, "departmental_exams/admin/cycle_defaults_confirm.html")
        applied = self.client.post(reverse("departmental_exams:cycle_apply_defaults", args=[cycle.id]), {
            "confirmation_state": confirmation.context["form"]["confirmation_state"].value()})
        self.assertEqual(applied.status_code, 302)
        cycle.refresh_from_db()
        self.assertEqual((cycle.default_questions_required_per_faculty, cycle.default_final_item_count), (60, 55))
        self.assertEqual(ExamGeneratorPolicy.objects.values().get(cycle=cycle), before_policy)

    def test_invalid_and_stale_policy_errors_stay_in_card_without_binding_defaults(self):
        cycle = self.make_cycle()
        url = self.cycle_url(cycle)
        data = {"action": "save_generator_policy", **self.policy_data()}
        data["mode"] = "INVALID"
        response = self.client.post(url, data)
        self.assertContains(response, "Select a valid choice")
        self.assertFalse(response.context["form"].is_bound)
        self.assertFalse(ExamGeneratorPolicy.objects.filter(cycle=cycle).exists())
        data["mode"] = "DIFFERENT"
        data["expected_revision"] = 42
        response = self.client.post(url, data)
        self.assertContains(response, "Generator policy changed. Reload before saving.", status_code=409)
        self.assertFalse(response.context["form"].is_bound)
        self.assertFalse(ExamGeneratorPolicy.objects.filter(cycle=cycle).exists())

    def test_invalid_defaults_keep_policy_unbound_and_saved_values(self):
        cycle = self.make_cycle()
        policy = ExamGeneratorPolicy.objects.create(cycle=cycle, departmental=True, mode="SAME_ORDER")
        before = ExamGeneratorPolicy.objects.values().get(pk=policy.id)
        data = self.defaults_data(cycle)
        data["default_final_item_count"] = 10
        response = self.client.post(self.cycle_url(cycle), data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(response.context["generator_form"].is_bound)
        self.assertEqual(ExamGeneratorPolicy.objects.values().get(pk=policy.id), before)

    def test_manage_cycles_direct_deny_and_other_tenant_fail_closed(self):
        cycle = self.make_cycle()
        url = self.cycle_url(cycle)
        self.client.force_login(self.manager)
        UserPermission.objects.create(user=self.manager, tenant=self.tenant,
            permission=Permission.objects.get(code="departmental_exams.manage_cycles"), grant_type="DENY")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"action":"save_generator_policy", **self.policy_data()}).status_code, 403)
        self.assertFalse(ExamGeneratorPolicy.objects.filter(cycle=cycle).exists())
        self.client.force_login(self.admin)
        other = type(cycle).objects.create(tenant=self.other_tenant, academic_year=self.year, term=self.term,
            exam_period="FINAL", created_by=self.admin)
        self.assertEqual(self.client.get(self.cycle_url(other)).status_code, 404)
        self.assertEqual(self.client.post(self.cycle_url(other), {"action":"save_generator_policy", **self.policy_data()}).status_code, 404)

    def test_closed_cycle_keeps_policy_visible_and_direct_urls_compatible(self):
        cycle = self.make_cycle()
        direct = reverse("departmental_exams:generator_policy", args=[cycle.id])
        self.assertContains(self.client.get(direct), "Save Generator Policy")
        self.assertRedirects(self.client.post(direct, self.policy_data()), direct)
        ExaminationCycleConfigurationService.open_cycle(cycle_id=cycle.id, tenant_id=self.tenant.id,
            user=self.admin, expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle))
        cycle.refresh_from_db()
        ExaminationCycleConfigurationService.close_cycle(cycle_id=cycle.id, tenant_id=self.tenant.id,
            user=self.admin, expected_updated_at=ExaminationCycleConfigurationService.transition_token(cycle))
        before = ExamGeneratorPolicy.objects.values().get(cycle=cycle)
        response = self.client.get(self.cycle_url(cycle))
        self.assertContains(response, "Exam Generator Config")
        self.assertTrue(response.context["generator_locked"])
        self.assertContains(response, "<fieldset disabled")
        self.assertContains(response, "Question Requirements")
        self.assertContains(response, "Automatic Generation")
        rejected = self.client.post(self.cycle_url(cycle), {"action":"save_generator_policy", **self.policy_data(1)})
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(ExamGeneratorPolicy.objects.values().get(cycle=cycle), before)

    def test_missing_policy_revision_error_is_visible_in_embedded_card(self):
        cycle = self.make_cycle()
        data = {"action":"save_generator_policy", **self.policy_data()}
        del data["expected_revision"]
        response = self.client.post(self.cycle_url(cycle), data)
        self.assertContains(response, "This field is required.")
        self.assertFalse(ExamGeneratorPolicy.objects.filter(cycle=cycle).exists())
        self.assertFalse(response.context["form"].is_bound)

    def test_automatic_defaults_pair_saves_without_changing_generator_policy(self):
        cycle = self.make_cycle()
        cycle.processing_mode = "AUTOMATIC_GENERATION"
        cycle.save(update_fields=["processing_mode"])
        policy = ExamGeneratorPolicy.objects.create(cycle=cycle, departmental=True, mode="SAME_ORDER")
        before = ExamGeneratorPolicy.objects.values().get(pk=policy.id)
        data = self.defaults_data(cycle)
        data.update({"automatic_campus_contribution_policy":"STRICT",
                     "automatic_contributor_completion_policy":"REQUIRE_ALL"})
        confirmation = self.client.post(self.cycle_url(cycle), data)
        self.assertTemplateUsed(confirmation, "departmental_exams/admin/cycle_defaults_confirm.html")
        applied = self.client.post(reverse("departmental_exams:cycle_apply_defaults", args=[cycle.id]), {
            "confirmation_state": confirmation.context["form"]["confirmation_state"].value()})
        self.assertEqual(applied.status_code, 302)
        cycle.refresh_from_db()
        self.assertEqual((cycle.automatic_campus_contribution_policy, cycle.automatic_contributor_completion_policy),
                         ("STRICT", "REQUIRE_ALL"))
        self.assertEqual(ExamGeneratorPolicy.objects.values().get(pk=policy.id), before)

    def test_automatic_cycle_alone_does_not_lock_policy(self):
        cycle = self.make_cycle()
        cycle.processing_mode = "AUTOMATIC_GENERATION"
        cycle.save(update_fields=["processing_mode"])
        response = self.client.post(reverse("departmental_exams:cycle_open", args=[cycle.id]), {
            "expected_updated_at": ExaminationCycleConfigurationService.transition_token(cycle)})
        self.assertEqual(response.status_code, 302)
        page = self.client.get(self.cycle_url(cycle))
        self.assertFalse(page.context["generator_locked"])
        self.assertRedirects(self.client.post(self.cycle_url(cycle), {
            "action":"save_generator_policy", **self.policy_data()}), self.cycle_url(cycle))
