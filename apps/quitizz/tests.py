from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.menu import MenuService
from apps.core.services.settings import SystemSettingService
from apps.navigation.models import MenuGroup, MenuItem
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Tenant

from .access import capabilities
from .forms import QuestionForm
from .models import QuiTizz, QuiTizzQuestion, QuiTizzSession, QuiTizzSessionQuestion
from .services import CONTENT_FIELDS, QuiTizzService, StaleRevision


class QuiTizzFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="QT", name="QuiTizz school")
        cls.campus = Campus.objects.create(tenant=cls.tenant, code="A", name="Campus A")
        cls.other_campus = Campus.objects.create(tenant=cls.tenant, code="B", name="Campus B")
        cls.other_tenant = Tenant.objects.create(code="OTHERQT", name="Other school")
        cls.foreign_campus = Campus.objects.create(tenant=cls.other_tenant, code="A", name="Foreign campus")
        cls.role = Role.objects.create(code="QUITIZZ_EMPLOYEE", name="Authorized employee")
        portal, _ = Permission.objects.get_or_create(code="faculty_portal.access", defaults={"module": "faculty_portal", "action": "access"})
        RolePermission.objects.create(role=cls.role, permission=portal)
        cls.user = cls.new_user("qt-owner")
        cls.other_user = cls.new_user("qt-other")
        cls.grant(cls.user, "quitizz.manage")
        cls.grant(cls.user, "quitizz.host")

    @classmethod
    def new_user(cls, username):
        user = User.objects.create_user(username=username, email=f"{username}@example.test", password="testpass", default_tenant=cls.tenant, default_campus=cls.campus,
            privacy_consent_version=settings.PRIVACY_CONSENT_VERSION, privacy_consent_at=timezone.now())
        UserRole.objects.create(user=user, role=cls.role, tenant=cls.tenant, campus=cls.campus)
        return user

    @classmethod
    def grant(cls, user, code, *, grant_type="ALLOW", tenant=None, campus=None):
        return UserPermission.objects.create(user=user, permission=Permission.objects.get(code=code), grant_type=grant_type,
            tenant=tenant or cls.tenant, campus=campus or cls.campus)

    def setUp(self):
        self.enable()
        self.client.force_login(self.user)

    def enable(self, enabled=True, tenant=None):
        SystemSettingService.set(FeatureSettingsService.QUITIZZ_ENABLED_KEY, enabled, tenant_id=(tenant or self.tenant).pk, value_type="BOOL")

    def args(self, user=None, tenant=None, campus=None):
        return {"user": user or self.user, "tenant_id": (tenant or self.tenant).pk, "campus_id": (campus or self.campus).pk}

    def quiz(self, title="Fiesta knowledge"):
        return QuiTizzService.create(**self.args(), title=title)

    def content(self, **changes):
        return {"prompt": "Which choice?", "choice_a": "Alpha", "choice_b": "Beta", "choice_c": "Gamma", "choice_d": "Delta", "correct_choice": "B", "timer_seconds": 30, **changes}

    def question(self, quiz, **changes):
        quiz.refresh_from_db()
        QuiTizzService.question_save(**self.args(), public_id=quiz.public_id, revision=quiz.revision, content=self.content(**changes))
        quiz.refresh_from_db()
        return quiz.questions.last()

    def mutate(self, quiz, **extra):
        quiz.refresh_from_db()
        return {**self.args(), "public_id": quiz.public_id, "revision": quiz.revision, **extra}

    def launch(self, quiz):
        return QuiTizzService.launch(**self.mutate(quiz))

    def url(self, name, quiz=None, question=None):
        kwargs = {"public_id": quiz.public_id} if quiz else {}
        if question:
            kwargs["question_id"] = question.pk
        return reverse(f"quitizz:{name}", kwargs=kwargs)

    def menu(self, user=None, campus=None):
        tree = MenuService.get_menu_tree(user or self.user, "FACULTY", self.tenant.pk, (campus or self.campus).pk)
        return [node["item"].code for group in tree if group["group"].code == "QUITIZZ" for node in group["items"]]

    def test_default_off(self):
        from apps.tenants.models import SystemSetting
        SystemSetting.objects.filter(setting_key=FeatureSettingsService.QUITIZZ_ENABLED_KEY).delete()
        self.assertFalse(FeatureSettingsService.is_quitizz_enabled(tenant_id=self.tenant.pk))
        self.assertFalse(FeatureSettingsService.is_quitizz_enabled(tenant_id=None))

    def test_global_toggle_and_tenant_override(self):
        from apps.tenants.models import SystemSetting
        SystemSetting.objects.filter(setting_key=FeatureSettingsService.QUITIZZ_ENABLED_KEY).delete()
        SystemSettingService.set(FeatureSettingsService.QUITIZZ_ENABLED_KEY, True, value_type="BOOL")
        self.assertTrue(FeatureSettingsService.is_quitizz_enabled(tenant_id=self.tenant.pk))
        self.enable(False)
        self.assertFalse(FeatureSettingsService.is_quitizz_enabled(tenant_id=self.tenant.pk))

    def test_off_hides_menu_and_blocks_all_routes(self):
        quiz = self.quiz()
        question = self.question(quiz)
        session = self.launch(quiz)
        self.enable(False)
        self.assertEqual(self.menu(), [])
        paths = [self.url("list"), self.url("create"), self.url("edit", quiz), self.url("question_add", quiz),
            self.url("question_edit", quiz, question), self.url("question_delete", quiz, question), self.url("launch", quiz), self.url("host", session)]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        for name in ("reorder", "archive", "launch"):
            self.assertEqual(self.client.post(self.url(name, quiz), {"revision": quiz.revision}).status_code, 403)

    def test_feature_on_does_not_grant_access(self):
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(self.url("list")).status_code, 403)
        self.assertEqual(self.menu(self.other_user), [])

    def test_superuser_requires_explicit_permissions(self):
        self.other_user.is_superuser = True
        self.other_user.save()
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(self.url("list")).status_code, 403)
        self.assertEqual(self.menu(self.other_user), [])

    def test_manager_menu_and_route_parity(self):
        UserPermission.objects.filter(user=self.user, permission__code="quitizz.host").delete()
        self.assertEqual(self.menu(), ["QUITIZZ_LIST", "QUITIZZ_CREATE"])
        self.assertContains(self.client.get(self.url("list")), "Create QuiTizz")
        quiz = self.quiz()
        self.assertEqual(self.client.get(self.url("launch", quiz)).status_code, 403)

    def test_host_only_can_launch_own_but_cannot_author(self):
        quiz = self.quiz()
        self.question(quiz)
        UserPermission.objects.filter(user=self.user, permission__code="quitizz.manage").delete()
        self.assertEqual(self.menu(), ["QUITIZZ_LIST"])
        response = self.client.get(self.url("list"))
        self.assertContains(response, "Host QuiTizz")
        self.assertNotContains(response, "Create QuiTizz")
        self.assertEqual(self.client.get(self.url("create")).status_code, 403)
        self.assertEqual(self.client.post(self.url("edit", quiz), {"title": "Forged", "revision": quiz.revision}).status_code, 403)
        self.assertEqual(self.client.post(self.url("launch", quiz), {"revision": quiz.revision}).status_code, 302)

    def test_direct_deny_wins_over_role_grant(self):
        RolePermission.objects.create(role=self.role, permission=Permission.objects.get(code="quitizz.manage"))
        self.grant(self.user, "quitizz.manage", grant_type="DENY")
        self.assertEqual(self.client.get(self.url("create")).status_code, 403)
        self.assertEqual(self.menu(), ["QUITIZZ_LIST"])

    def test_arbitrary_role_grant_allows_authoring(self):
        UserPermission.objects.filter(user=self.user, permission__code="quitizz.manage").delete()
        RolePermission.objects.create(role=self.role, permission=Permission.objects.get(code="quitizz.manage"))
        self.assertEqual(self.client.post(self.url("create"), {"title": "Role granted"}).status_code, 302)

    def test_inactive_permission_blocks_route_and_menu(self):
        Permission.objects.filter(code__in=["quitizz.manage", "quitizz.host"]).update(is_active=False)
        self.assertEqual(self.menu(), [])
        self.assertEqual(self.client.get(self.url("list")).status_code, 403)

    def test_broad_direct_deny_wins(self):
        UserPermission.objects.create(user=self.user, permission=Permission.objects.get(code="quitizz.host"), grant_type="DENY")
        quiz = self.quiz()
        self.assertEqual(self.client.get(self.url("launch", quiz)).status_code, 403)
        self.assertFalse(capabilities(self.user, self.tenant.pk, self.campus.pk)["host"])

    def test_portal_deny_hides_menu(self):
        self.grant(self.user, "faculty_portal.access", grant_type="DENY")
        self.assertEqual(self.menu(), [])
        self.assertEqual(self.client.get(self.url("list")).status_code, 403)

    def test_null_scope_grant_does_not_allow_cross_campus(self):
        UserPermission.objects.create(user=self.other_user, permission=Permission.objects.get(code="quitizz.manage"))
        self.assertEqual(self.menu(self.other_user), [])

    def test_wrong_campus_grant_rejected(self):
        self.grant(self.other_user, "quitizz.manage", campus=self.other_campus)
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(self.url("create")).status_code, 403)

    def test_inactive_campus_rejected(self):
        self.campus.is_active = False
        self.campus.save()
        self.assertEqual(self.menu(), [])
        self.assertEqual(self.client.get(self.url("list")).status_code, 403)

    def test_missing_scope_rejected(self):
        with self.assertRaises(PermissionDenied):
            QuiTizzService.create(user=self.user, tenant_id=None, campus_id=None, title="Unsafe")

    def test_history_permission_reserved(self):
        self.grant(self.other_user, "quitizz.view_history")
        self.assertEqual(self.menu(self.other_user), [])
        self.assertFalse(any(capabilities(self.other_user, self.tenant.pk, self.campus.pk).values()))

    def test_no_automatic_role_grants_or_admin_authoring(self):
        self.assertFalse(RolePermission.objects.filter(permission__code__startswith="quitizz.").exists())
        self.assertFalse(MenuItem.objects.filter(portal="ADMIN", route_name__startswith="quitizz:").exists())
        self.assertTrue(MenuGroup.objects.filter(portal="FACULTY", code="QUITIZZ").exists())

    def test_create_authoring_http_and_audit(self):
        response = self.client.post(self.url("create"), {"title": "Campus sparks", "tenant": self.other_tenant.pk, "owner": self.other_user.pk})
        self.assertEqual(response.status_code, 302)
        quiz = QuiTizz.objects.get()
        self.assertEqual((quiz.owner_id, quiz.tenant_id, quiz.campus_id), (self.user.pk, self.tenant.pk, self.campus.pk))
        self.assertTrue(AuditLog.objects.filter(action="QUITIZZ_CREATE", tenant=self.tenant, campus=self.campus, actor_user=self.user).exists())

    def test_blank_title_rejected(self):
        self.assertEqual(self.client.post(self.url("create"), {"title": "  "}).status_code, 400)
        self.assertFalse(QuiTizz.objects.exists())

    def test_edit_title_revision_and_audit(self):
        quiz = self.quiz()
        self.assertEqual(self.client.post(self.url("edit", quiz), {"title": "New title", "revision": quiz.revision}).status_code, 302)
        quiz.refresh_from_db()
        self.assertEqual((quiz.title, quiz.revision), ("New title", 2))
        self.assertTrue(AuditLog.objects.filter(action="QUITIZZ_EDIT").exists())

    def test_stale_edit_rejected(self):
        quiz = self.quiz()
        QuiTizzService.edit(**self.mutate(quiz), title="New")
        response = self.client.post(self.url("edit", quiz), {"title": "Stale", "revision": 1})
        self.assertEqual(response.status_code, 409)
        quiz.refresh_from_db()
        self.assertEqual(quiz.title, "New")

    def test_missing_revision_rejected(self):
        quiz = self.quiz()
        self.assertEqual(self.client.post(self.url("edit", quiz), {"title": "Tampered"}).status_code, 400)

    def test_owner_isolation_on_list_edit_launch_and_post(self):
        quiz = self.quiz()
        question = self.question(quiz)
        session = self.launch(quiz)
        self.grant(self.other_user, "quitizz.manage")
        self.grant(self.other_user, "quitizz.host")
        self.client.force_login(self.other_user)
        self.assertNotContains(self.client.get(self.url("list")), quiz.title)
        for name, obj in (("edit", quiz), ("launch", quiz), ("host", session)):
            self.assertEqual(self.client.get(self.url(name, obj)).status_code, 404)
        self.assertEqual(self.client.post(self.url("question_delete", quiz, question), {"revision": quiz.revision}).status_code, 404)

    def test_cross_tenant_object_ids_rejected(self):
        foreign = QuiTizz.objects.create(tenant=self.other_tenant, campus=self.foreign_campus, owner=self.user, title="Foreign")
        self.assertEqual(self.client.get(self.url("edit", foreign)).status_code, 404)
        self.assertEqual(self.client.post(self.url("launch", foreign), {"revision": foreign.revision}).status_code, 404)

    def test_cross_campus_object_ids_rejected(self):
        other = QuiTizz.objects.create(tenant=self.tenant, campus=self.other_campus, owner=self.user, title="Other campus")
        self.assertEqual(self.client.get(self.url("edit", other)).status_code, 404)
        self.assertEqual(self.client.post(self.url("archive", other), {"revision": other.revision, "archived": "1"}).status_code, 404)

    def test_cross_scope_host_session_ids_rejected(self):
        for tenant, campus in ((self.tenant, self.other_campus), (self.other_tenant, self.foreign_campus)):
            source = QuiTizz.objects.create(tenant=tenant, campus=campus, owner=self.user, title="Other scope")
            session = QuiTizzSession.objects.create(source=source, tenant=tenant, campus=campus, host=self.user, title_snapshot=source.title, source_revision=1)
            self.assertEqual(self.client.get(self.url("host", session)).status_code, 404)

    def test_model_rejects_mismatched_tenant_campus(self):
        with self.assertRaises(ValidationError):
            QuiTizz.objects.create(tenant=self.tenant, campus=self.foreign_campus, owner=self.user, title="Mismatch")

    def test_question_http_create_and_edit(self):
        quiz = self.quiz()
        self.assertEqual(self.client.post(self.url("question_add", quiz), {**self.content(), "revision": quiz.revision}).status_code, 302)
        question = quiz.questions.get()
        quiz.refresh_from_db()
        self.assertEqual(self.client.post(self.url("question_edit", quiz, question), {**self.content(prompt="Changed"), "revision": quiz.revision}).status_code, 302)
        question.refresh_from_db()
        self.assertEqual(question.prompt, "Changed")
        self.assertTrue(AuditLog.objects.filter(action="QUITIZZ_QUESTION_CREATE").exists())
        self.assertTrue(AuditLog.objects.filter(action="QUITIZZ_QUESTION_EDIT").exists())

    def test_all_four_choices_required(self):
        for field in ("choice_a", "choice_b", "choice_c", "choice_d"):
            with self.subTest(field=field):
                form = QuestionForm(data=self.content(**{field: " "}))
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_service_requires_exact_payload(self):
        quiz = self.quiz()
        for content in ({**self.content(), "choice_e": "Extra"}, {key: value for key, value in self.content().items() if key != "choice_d"}):
            with self.assertRaises(ValidationError):
                QuiTizzService.question_save(**self.mutate(quiz), content=content)
        self.assertFalse(quiz.questions.exists())

    def test_invalid_correct_answer_and_timer_http(self):
        quiz = self.quiz()
        for changes in ({"correct_choice": "E"}, {"correct_choice": "<script>"}, {"timer_seconds": 0}, {"timer_seconds": -1}, {"timer_seconds": "NaN"}, {"prompt": " "}):
            with self.subTest(changes=changes):
                response = self.client.post(self.url("question_add", quiz), {**self.content(**changes), "revision": quiz.revision})
                self.assertEqual(response.status_code, 400)
        self.assertFalse(quiz.questions.exists())

    def test_db_answer_timer_and_position_constraints(self):
        quiz = self.quiz()
        question = self.question(quiz)
        for change in ({"correct_choice": "E"}, {"timer_seconds": 0}, {"position": 0}, {"choice_a": ""}):
            with self.subTest(change=change), self.assertRaises(IntegrityError), transaction.atomic():
                QuiTizzQuestion.objects.filter(pk=question.pk).update(**change)

    def test_unique_question_position(self):
        quiz = self.quiz()
        self.question(quiz)
        with self.assertRaises(ValidationError):
            QuiTizzQuestion.objects.create(quitizz=quiz, position=1, **self.content())

    def test_reorder_deterministic_and_audited(self):
        quiz = self.quiz()
        first = self.question(quiz, prompt="First")
        second = self.question(quiz, prompt="Second")
        QuiTizzService.reorder(**self.mutate(quiz), question_ids=[second.pk, first.pk])
        self.assertEqual(list(quiz.questions.values_list("pk", "position")), [(second.pk, 1), (first.pk, 2)])
        self.assertTrue(AuditLog.objects.filter(action="QUITIZZ_REORDER").exists())

    def test_forged_reorder_is_atomic(self):
        quiz = self.quiz()
        first = self.question(quiz)
        second = self.question(quiz)
        foreign = self.question(self.quiz("Other"))
        initial = list(quiz.questions.values_list("pk", "position"))
        for ids in ([first.pk], [first.pk, first.pk], [first.pk, foreign.pk], ["bad", second.pk]):
            with self.assertRaises(ValidationError):
                QuiTizzService.reorder(**self.mutate(quiz), question_ids=ids)
            self.assertEqual(list(quiz.questions.values_list("pk", "position")), initial)

    def test_reorder_post_and_get_guard(self):
        quiz = self.quiz()
        a = self.question(quiz)
        b = self.question(quiz)
        self.assertEqual(self.client.get(self.url("reorder", quiz)).status_code, 405)
        self.assertEqual(self.client.post(self.url("reorder", quiz), {"revision": quiz.revision, "question_ids": [b.pk, a.pk]}).status_code, 302)
        self.assertEqual(list(quiz.questions.values_list("pk", flat=True)), [b.pk, a.pk])

    def test_delete_confirmation_and_resequence(self):
        quiz = self.quiz()
        a = self.question(quiz)
        b = self.question(quiz)
        self.assertContains(self.client.get(self.url("question_delete", quiz, a)), "Delete question")
        self.assertEqual(quiz.questions.count(), 2)
        self.assertEqual(self.client.post(self.url("question_delete", quiz, a), {"revision": quiz.revision}).status_code, 302)
        b.refresh_from_db()
        self.assertEqual(b.position, 1)
        self.assertTrue(AuditLog.objects.filter(action="QUITIZZ_QUESTION_DELETE").exists())

    def test_foreign_question_id_rejected(self):
        quiz = self.quiz()
        foreign = self.question(self.quiz("Other"))
        for name in ("question_edit", "question_delete"):
            self.assertEqual(self.client.post(self.url(name, quiz, foreign), {**self.content(), "revision": quiz.revision}).status_code, 404)

    def test_archive_reactivate_preserves_history(self):
        quiz = self.quiz()
        self.question(quiz)
        session = self.launch(quiz)
        QuiTizzService.archive(**self.mutate(quiz), archived=True)
        with self.assertRaises(ValidationError):
            self.launch(quiz)
        with self.assertRaises(ValidationError):
            QuiTizzService.edit(**self.mutate(quiz), title="Blocked")
        QuiTizzService.archive(**self.mutate(quiz), archived=False)
        self.launch(quiz)
        self.assertEqual(QuiTizzSession.objects.count(), 2)
        self.assertEqual(session.questions.count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="QUITIZZ_ARCHIVE").exists())
        self.assertTrue(AuditLog.objects.filter(action="QUITIZZ_REACTIVATE").exists())

    def test_archive_post_validation(self):
        quiz = self.quiz()
        self.assertEqual(self.client.get(self.url("archive", quiz)).status_code, 405)
        self.assertEqual(self.client.post(self.url("archive", quiz), {"revision": quiz.revision, "archived": "evil"}).status_code, 400)
        self.assertEqual(self.client.post(self.url("archive", quiz), {"revision": quiz.revision, "archived": "1"}).status_code, 302)
        quiz.refresh_from_db()
        self.assertTrue(quiz.is_archived)

    def test_launch_snapshots_exact_content_and_order(self):
        quiz = self.quiz()
        first = self.question(quiz, prompt="First", correct_choice="A", timer_seconds=15)
        second = self.question(quiz, prompt="Second", correct_choice="D", timer_seconds=60)
        QuiTizzService.reorder(**self.mutate(quiz), question_ids=[second.pk, first.pk])
        session = self.launch(quiz)
        quiz.refresh_from_db()
        self.assertEqual((session.title_snapshot, session.source_revision, session.host_id, session.status), (quiz.title, quiz.revision, self.user.pk, "READY"))
        self.assertEqual(list(session.questions.values_list("prompt", "correct_choice", "timer_seconds", "position")), [("Second", "D", 60, 1), ("First", "A", 15, 2)])
        for source, saved in zip(quiz.questions.all(), session.questions.all()):
            self.assertEqual([getattr(source, key) for key in CONTENT_FIELDS], [getattr(saved, key) for key in CONTENT_FIELDS])
        self.assertEqual(session.scoring_policy_snapshot, {})
        self.assertTrue(AuditLog.objects.filter(action="QUITIZZ_SESSION_LAUNCH", entity_id=str(session.pk)).exists())

    def test_launch_get_read_only_and_post_host_screen(self):
        quiz = self.quiz()
        self.question(quiz)
        self.assertContains(self.client.get(self.url("launch", quiz)), "Launch QuiTizz Session")
        self.assertFalse(QuiTizzSession.objects.exists())
        response = self.client.post(self.url("launch", quiz), {"revision": quiz.revision}, follow=True)
        self.assertContains(response, "QuiTizz Session")
        self.assertContains(response, "Powered by NCBA TeacherMate+")

    def test_source_edit_delete_archive_preserves_snapshots(self):
        quiz = self.quiz()
        question = self.question(quiz)
        session = self.launch(quiz)
        initial = list(session.questions.values(*CONTENT_FIELDS, "position"))
        QuiTizzService.edit(**self.mutate(quiz), title="Changed title")
        QuiTizzService.question_save(**self.mutate(quiz), question_id=question.pk, content=self.content(prompt="Changed source"))
        QuiTizzService.question_delete(**self.mutate(quiz), question_id=question.pk)
        QuiTizzService.archive(**self.mutate(quiz), archived=True)
        session.refresh_from_db()
        self.assertEqual(session.title_snapshot, "Fiesta knowledge")
        self.assertEqual(list(session.questions.values(*CONTENT_FIELDS, "position")), initial)

    def test_independent_multiple_launches(self):
        quiz = self.quiz()
        question = self.question(quiz)
        first = self.launch(quiz)
        QuiTizzService.question_save(**self.mutate(quiz), question_id=question.pk, content=self.content(prompt="New launch"))
        second = self.launch(quiz)
        third = self.launch(quiz)
        self.assertEqual(first.questions.get().prompt, "Which choice?")
        self.assertEqual(second.questions.get().prompt, "New launch")
        self.assertNotEqual(second.public_id, third.public_id)
        self.assertNotEqual(second.questions.get().pk, third.questions.get().pk)

    def test_empty_and_stale_launch_rejected(self):
        quiz = self.quiz()
        with self.assertRaises(ValidationError):
            self.launch(quiz)
        self.question(quiz)
        with self.assertRaises(StaleRevision):
            QuiTizzService.launch(**self.args(), public_id=quiz.public_id, revision=1)
        self.assertFalse(QuiTizzSession.objects.exists())

    def test_snapshot_failure_rolls_back_complete_launch(self):
        quiz = self.quiz()
        self.question(quiz)
        self.question(quiz)
        def partial_then_fail(rows):
            rows[0].save()
            raise RuntimeError("Injected after first snapshot write")
        with patch.object(QuiTizzSessionQuestion.objects, "bulk_create", side_effect=partial_then_fail), self.assertRaises(RuntimeError):
            self.launch(quiz)
        self.assertEqual(QuiTizzSession.objects.count(), 0)
        self.assertEqual(QuiTizzSessionQuestion.objects.count(), 0)
        self.assertFalse(AuditLog.objects.filter(action="QUITIZZ_SESSION_LAUNCH").exists())

    def test_audit_failure_rolls_back_launch(self):
        quiz = self.quiz()
        self.question(quiz)
        with patch("apps.quitizz.services.AuditService.log_event", side_effect=RuntimeError("Audit write failed")), self.assertRaises(RuntimeError):
            self.launch(quiz)
        self.assertFalse(QuiTizzSession.objects.exists())
        self.assertFalse(QuiTizzSessionQuestion.objects.exists())

    def test_snapshots_reject_model_and_queryset_mutation(self):
        quiz = self.quiz()
        self.question(quiz)
        session = self.launch(quiz)
        question = session.questions.get()
        for obj in (session, question):
            with self.assertRaises(ValidationError):
                obj.save()
            with self.assertRaises(ValidationError):
                obj.delete()
            with self.assertRaises(ValidationError):
                type(obj).objects.filter(pk=obj.pk).update(id=obj.pk)
            with self.assertRaises(ValidationError):
                type(obj).objects.filter(pk=obj.pk).delete()
            with self.assertRaises(ValidationError):
                type(obj).objects.bulk_update([obj], ["updated_at"])

    def test_no_question_count_cap(self):
        quiz = self.quiz()
        for i in range(26):
            QuiTizzQuestion.objects.create(quitizz=quiz, position=i+1, **self.content(prompt=str(i)))
        self.assertEqual(self.launch(quiz).questions.count(), 26)

    def test_launched_session_rejects_appended_snapshot(self):
        quiz = self.quiz()
        self.question(quiz)
        session = self.launch(quiz)
        with self.assertRaises(ValidationError):
            QuiTizzSessionQuestion.objects.create(session=session, position=2, **self.content())
        with self.assertRaises(ValidationError):
            QuiTizzSessionQuestion.objects.bulk_create([QuiTizzSessionQuestion(session=session, position=2, **self.content())])
        self.assertEqual(session.questions.count(), 1)

    def test_unauthorized_posts_cannot_write(self):
        quiz = self.quiz()
        question = self.question(quiz)
        self.client.force_login(self.other_user)
        paths = [self.url("create"), self.url("edit", quiz), self.url("question_add", quiz), self.url("question_edit", quiz, question), self.url("question_delete", quiz, question), self.url("reorder", quiz), self.url("archive", quiz), self.url("launch", quiz)]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, {**self.content(), "title": "Forged", "revision": quiz.revision}).status_code, 403)
        self.assertEqual(QuiTizz.objects.count(), 1)
        self.assertEqual(QuiTizzQuestion.objects.count(), 1)
        self.assertFalse(QuiTizzSession.objects.exists())

    def test_csrf_rejects_crafted_post(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url("create"), {"title": "Forged"}).status_code, 403)

    def test_authoring_pages_escape_content(self):
        quiz = self.quiz("<script>alert(1)</script>")
        self.question(quiz, prompt="<img src=x onerror=alert(1)>")
        for path in (self.url("list"), self.url("edit", quiz), self.url("question_add", quiz)):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "<script>alert(1)</script>")
            self.assertNotContains(response, "<img src=x onerror=alert(1)>")

    def test_admin_toggle_get_post_and_tenant_isolation(self):
        from apps.admin_portal.forms import ConfigurableFeatureSettingForm
        for code in ("admin_portal.access", "system_settings.update"):
            permission, _ = Permission.objects.get_or_create(code=code, defaults={"module": "system_settings", "action": code.split(".")[-1]})
            UserPermission.objects.create(user=self.user, permission=permission, tenant=self.tenant, campus=self.campus)
        url = reverse("admin_portal:configurable_features_settings")
        response = self.client.get(url)
        self.assertContains(response, "Enable QuiTizz")
        self.assertContains(response, "feature-card-quitizz")
        form = response.context["form"]
        payload = {}
        for name, field in form.fields.items():
            value = form[name].value()
            if value is not None and value is not False:
                payload[name] = value
        payload["quitizz_enabled"] = ""
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertFalse(FeatureSettingsService.is_quitizz_enabled(tenant_id=self.tenant.pk))
        payload["quitizz_enabled"] = "on"
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertTrue(FeatureSettingsService.is_quitizz_enabled(tenant_id=self.tenant.pk))
        self.assertFalse(FeatureSettingsService.is_quitizz_enabled(tenant_id=self.other_tenant.pk))
        self.assertFalse(ConfigurableFeatureSettingForm.base_fields["quitizz_enabled"].required)

    def test_service_feature_and_permission_rechecked(self):
        quiz = self.quiz()
        self.question(quiz)
        self.enable(False)
        with self.assertRaises(PermissionDenied):
            self.launch(quiz)
        self.enable()
        self.grant(self.user, "quitizz.host", grant_type="DENY")
        with self.assertRaises(PermissionDenied):
            self.launch(quiz)

    def test_faculty_guide_explains_authoring_and_snapshot_boundaries(self):
        response = self.client.get(reverse("faculty_portal:guide"))
        self.assertContains(response, "Create and Host QuiTizz")
        self.assertContains(response, "existing sessions retain their snapshots")
        self.assertContains(response, "Participant joining and gameplay are not available")

    def test_admin_guide_limits_responsibility_to_configuration(self):
        from apps.admin_portal.help_guide import build_admin_help_sections
        Permission.objects.get_or_create(code="system_settings.update", defaults={"module": "system_settings", "action": "update"})
        self.grant(self.user, "system_settings.update")
        sections = build_admin_help_sections(user=self.user, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
        quitizz = next(section for section in sections if section["code"] == "quitizz-configuration")
        self.assertIn("Configurable Features", quitizz["topics"][0]["actions"][0]["name"])

    def test_seed_reverse_disables_without_losing_assignments_and_reseed_restores(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        seed_permissions = import_module("apps.quitizz.migrations.0002_seed_permissions")
        seed_navigation = import_module("apps.quitizz.migrations.0003_seed_faculty_navigation")
        editor = SimpleNamespace(connection=SimpleNamespace(alias="default"))
        grants = set(UserPermission.objects.filter(user=self.user).values_list("pk", flat=True))
        seed_navigation.unseed(apps, editor)
        seed_permissions.unseed(apps, editor)
        self.assertEqual(self.menu(), [])
        self.assertEqual(set(UserPermission.objects.filter(user=self.user).values_list("pk", flat=True)), grants)
        seed_permissions.seed(apps, editor)
        seed_navigation.seed(apps, editor)
        self.assertEqual(self.menu(), ["QUITIZZ_LIST", "QUITIZZ_CREATE"])
