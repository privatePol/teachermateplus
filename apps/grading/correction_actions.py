"""Correction action identities and signing evidence; no grade computation."""
import hashlib
import json
from functools import wraps
from uuid import UUID, uuid4

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import UserSignatureCredential
from apps.accounts.services import UserSignatureService
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.grading.models import CorrectionActionReceipt, GradeCorrectionAttachment, GradeCorrectionSignatureEvidence
from apps.rbac.models import UserPermission


class CorrectionSignatureRequired(ValidationError):
    def __init__(self):
        super().__init__(
            "A usable registered e-signature is required for this action. Open My Signature, "
            "save your signature, then return to this form and try again.", code="signature_required",
        )


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def assert_not_denied(user, offering, codes):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied("An active authenticated actor is required.")
    denied = PermissionService._scoped_user_permissions(
        user, offering.tenant_id, offering.campus_id,
    ).filter(permission__code__in=codes, grant_type=UserPermission.GrantType.DENY).exists()
    if denied:
        raise PermissionDenied("This correction action is denied in the target scope.")


def action_context(*, kind, user, offering, period, request_id=None, step_id=None):
    return dict(kind=kind, actor=user.pk, tenant=offering.tenant_id, campus=offering.campus_id,
                offering=offering.pk, period=period.pk, request=request_id, step=step_id)


def new_action_token(**kwargs):
    return signing.dumps({**action_context(**kwargs), "key": str(uuid4())}, salt="correction-action-v1")


def token_key(token, **kwargs):
    try:
        data = signing.loads(token, salt="correction-action-v1")
        if {k: data.get(k) for k in action_context(**kwargs)} != action_context(**kwargs):
            raise ValueError
        return UUID(data["key"])
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        raise ValidationError("Invalid correction action identity. Reopen the form to start a new action.") from None


def signature_management_context(view):
    """Allow an authorized target tenant's setup without trusting a tenant query ID.

    Keep the signed context briefly in this authenticated session so the existing
    profile preview and post-save redirects work without another registration UI.
    """
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return view(request, *args, **kwargs)
        saved = request.session.get("correction_signature_context", {})
        token = request.GET.get("correction_context")
        if not token and saved.get("expires", 0) > timezone.now().timestamp():
            token = saved.get("token")
        if token:
            from apps.academics.models import CourseOffering
            from apps.grading.models import GradeCorrectionRequest
            from apps.grading.services import GradingGovernanceService
            from apps.core.services.scope import ScopeService
            try:
                data = signing.loads(token, salt="correction-action-v1")
                if data["actor"] != request.user.pk:
                    raise ValueError
                offering = CourseOffering.objects.get(pk=data["offering"], tenant_id=data["tenant"],
                    campus_id=data["campus"], is_active=True)
                if data["kind"] == "SUBMIT":
                    CorrectionSubmissionService.authorize(request.user, offering)
                elif data["kind"] == "ON_BEHALF":
                    AdminOnBehalfSubmissionService.authorize(request.user, offering)
                elif data["kind"] == "REVIEW":
                    for code in ("admin_portal.access", "corrections.review"):
                        if not PermissionService.has_permission(request.user, code, offering.tenant_id, offering.campus_id):
                            raise PermissionDenied
                    petition = GradeCorrectionRequest.objects.get(pk=data["request"], offering=offering,
                        template_period_id=data["period"])
                    step = petition.approval_steps.get(pk=data["step"])
                    if not GradingGovernanceService._user_has_role_for_correction_step(
                            user=request.user, request_obj=petition, step=step):
                        raise PermissionDenied
                else:
                    raise ValueError
                scope = ScopeService.build_scope(request.user, tenant_id=offering.tenant_id, campus_id=offering.campus_id)
                if scope.get("tenant_id") != offering.tenant_id or scope.get("campus_id") != offering.campus_id:
                    raise PermissionDenied("Signature setup is outside your authorized scope.")
                request.scope = scope
                request.session["correction_signature_context"] = {"token": token, "expires": timezone.now().timestamp() + 600}
            except (signing.BadSignature, KeyError, TypeError, ValueError, ObjectDoesNotExist):
                raise PermissionDenied("Invalid signature setup context.") from None
        return view(request, *args, **kwargs)
    return wrapped


def usable_credential(*, user, tenant_id):
    # Read afresh under the same lock used by replacement/removal, never the user cache.
    if not FeatureSettingsService.is_user_signatures_enabled(tenant_id=tenant_id):
        raise CorrectionSignatureRequired()
    credential = UserSignatureCredential.objects.select_for_update().filter(user_id=user.pk).first()
    try:
        raw = UserSignatureService.decrypt_signature_bytes(credential=credential)
        if hashlib.sha256(raw).hexdigest() != credential.content_sha256:
            raise ValueError
        image = UserSignatureService._normalize_image_bytes(raw)
        if (credential.image_width, credential.image_height) != (image.width, image.height):
            raise ValueError
    except Exception:
        # Do not expose ciphertext, key details, or image parser errors to the caller.
        raise CorrectionSignatureRequired() from None
    return credential


def capture_signature(*, credential, request_obj, signer, action, role, payload, step=None, at=None):
    return GradeCorrectionSignatureEvidence.objects.create(
        action_key=f"{action}:{step.pk if step else request_obj.pk}", correction_request=request_obj,
        approval_step=step, signer=signer, tenant_id=request_obj.tenant_id, campus_id=request_obj.campus_id,
        action=action, signer_name=signer.full_name or signer.get_full_name() or signer.username,
        signing_role=role, signed_at=at or timezone.now(), encrypted_blob=bytes(credential.encrypted_blob),
        encryption_nonce=bytes(credential.encryption_nonce), content_sha256=credential.content_sha256,
        image_width=credential.image_width, image_height=credential.image_height,
        action_payload_sha256=digest(payload),
    )


def claim_receipt(*, key, payload_hash, kind, user, offering, period, request_obj=None, step_id=None):
    """INSERT first: the unique key arbitrates concurrent transactions on every DB.

    The savepoint lets a duplicate wait for its owner and then use a current locked
    read (also on InnoDB REPEATABLE READ). A failed owner rolls back the identity.
    """
    expected = dict(kind=kind, actor_id=user.pk, tenant_id=offering.tenant_id,
                    campus_id=offering.campus_id, offering_id=offering.pk,
                    template_period_id=period.pk, payload_sha256=payload_hash)
    try:
        with transaction.atomic():
            receipt = CorrectionActionReceipt.objects.create(
                key=key, **expected, correction_request=request_obj, approval_step_id=step_id,
            )
        return receipt, True
    except IntegrityError:
        receipt = CorrectionActionReceipt.objects.select_for_update().filter(pk=key).first()
        if receipt is None:
            raise
        if (any(getattr(receipt, k) != v for k, v in expected.items()) or receipt.approval_step_id != step_id
                or (request_obj and receipt.correction_request_id != request_obj.pk)):
            raise ValidationError("This action identity was already used with different correction data.") from None
        if not receipt.correction_request_id:
            raise ValidationError("This correction action has not completed. Please retry.")
        return receipt, False


class CorrectionSubmissionService:
    kind = "SUBMIT"
    portal = "FACULTY"
    audit_action = "CREATE"

    @staticmethod
    def payload(post, files):
        attachment = files.get("attachment")
        file_data = None
        if attachment:
            position = attachment.tell()
            h = hashlib.sha256()
            for chunk in attachment.chunks():
                h.update(chunk)
            attachment.seek(position)
            file_data = [attachment.name, attachment.size, h.hexdigest()]
        # Ignore CSRF and the token itself; retain all semantically relevant inputs.
        return dict(students=sorted(post.getlist("students")), activities=sorted(post.getlist("grade_activities")),
                    all_students=post.get("apply_to_all_students", ""),
                    grid=post.get("correction_payload", ""), justification=post.get("justification", ""),
                    attachment=file_data)

    @staticmethod
    def authorize(user, offering):
        from apps.academics.models import FacultyAssignment
        if not user.is_authenticated or not user.is_active:
            raise PermissionDenied("An active faculty account is required.")
        for code in ("faculty_portal.access", "corrections.create"):
            if not PermissionService.has_permission(user, code, offering.tenant_id, offering.campus_id):
                raise PermissionDenied("Correction filing is not permitted in this scope.")
        if not FacultyAssignment.objects.filter(offering=offering, faculty_user=user,
                is_active=True, accepted_at__isnull=False).exists():
            raise PermissionDenied("An accepted faculty assignment is required.")

    @classmethod
    def recover(cls, *, token, payload, user, offering, period):
        cls.authorize(user, offering)
        key = token_key(token, kind=cls.kind, user=user, offering=offering, period=period)
        receipt = CorrectionActionReceipt.objects.filter(pk=key).first()
        if receipt:
            expected = (user.pk, offering.tenant_id, offering.campus_id, offering.pk, period.pk, digest(payload))
            actual = (receipt.actor_id, receipt.tenant_id, receipt.campus_id, receipt.offering_id,
                      receipt.template_period_id, receipt.payload_sha256)
            if receipt.kind != cls.kind or actual != expected:
                raise ValidationError("This submission identity was already used with different correction data.")
            request_obj = receipt.correction_request
            owner_id = (request_obj.initiated_by_user_id if cls.kind == "ON_BEHALF" else request_obj.requested_by_user_id) if request_obj else None
            if not request_obj or owner_id != user.pk:
                raise ValidationError("This submission has not completed. Please retry.")
            return request_obj
        return None

    @classmethod
    def submit(cls, *, token, payload, user, offering, period, form, request=None, faculty=None, on_behalf_reason=""):
        from apps.grading.services import GradingGovernanceService
        cls.authorize(user, offering)
        key = token_key(token, kind=cls.kind, user=user, offering=offering, period=period)
        creation_options = {}
        if cls.kind == "ON_BEHALF":
            if (not faculty or payload.get("faculty") != str(faculty.pk)
                    or payload.get("on_behalf_reason") != on_behalf_reason.strip()):
                raise ValidationError("The administrative petitioner context does not match the submitted data.")
            creation_options = dict(initiated_by_user=user, request_source="ADMIN_ON_BEHALF", on_behalf_reason=on_behalf_reason)
        stored_file = None
        try:
            with transaction.atomic():
                receipt, created = claim_receipt(key=key, payload_hash=digest(payload), kind=cls.kind,
                    user=user, offering=offering, period=period)
                if not created:
                    return receipt.correction_request, False
                correction = GradingGovernanceService.create_correction_request(
                    user=faculty if cls.kind == "ON_BEHALF" else user, offering=offering, template_period=period,
                    justification=form.cleaned_data["justification"], items=form.cleaned_data["items"],
                    dispatch_notifications=False,
                    **creation_options,
                )
                attachment = form.cleaned_data.get("attachment")
                if attachment:
                    metadata = form.cleaned_data["attachment_validation"]
                    row = GradeCorrectionAttachment(correction_request=correction, uploaded_by_user=user,
                        original_filename=metadata.original_filename, content_type=metadata.content_type,
                        file_size_bytes=metadata.file_size_bytes)
                    # Retain the storage-assigned name even if the subsequent DB save fails.
                    row.file.save(attachment.name, attachment, save=False)
                    stored_file = (row.file.storage, row.file.name)
                    row.save()
                    AuditService.log_event(action="UPLOAD_CORRECTION_ATTACHMENT", portal=cls.portal,
                        entity_type="GradeCorrectionAttachment", entity_id=row.pk, actor=user,
                        tenant=offering.tenant, campus=offering.campus,
                        after_data={"correction_request_id": correction.pk, "original_filename": row.original_filename,
                                    "stored_filename": row.file.name, "content_type": row.content_type,
                                    "file_size_bytes": row.file_size_bytes,
                                    **({"on_behalf": True} if cls.kind == "ON_BEHALF" else {})}, request=request)
                administrative_data = ({"requested_by_user_id": faculty.pk, "initiated_by_user_id": user.pk,
                    "request_source": correction.request_source, "on_behalf_reason": correction.on_behalf_reason}
                    if cls.kind == "ON_BEHALF" else {})
                AuditService.log_event(action=cls.audit_action, portal=cls.portal, entity_type="GradeCorrectionRequest",
                    entity_id=correction.pk, actor=user, tenant=offering.tenant, campus=offering.campus,
                    after_data={"offering_id": offering.pk, "period_id": period.pk,
                                "requested_action": "UPDATE_SCORE",
                                "correction_item_count": len(form.cleaned_data["items"]),
                                "student_count": len({item["student_id"] for item in form.cleaned_data["items"]}),
                                "grading_item_count": len({item["grade_activity_id"] for item in form.cleaned_data["items"]}),
                                "approval_notification_email_attempted": 0,
                                "approval_notification_email_sent": 0,
                                "approval_notification_email_recipients": [],
                                "notification_delivery": "durable_outbox", **administrative_data}, request=request)
                receipt.correction_request = correction
                receipt.save(update_fields=["correction_request"])
                return correction, True
        except Exception:
            if stored_file:
                try:
                    stored_file[0].delete(stored_file[1])
                except Exception:
                    import logging
                    logging.getLogger("teachermateplus.system").error("Correction attachment rollback cleanup failed; storage reconciliation required.")
            raise


class AdminOnBehalfSubmissionService(CorrectionSubmissionService):
    kind = "ON_BEHALF"
    portal = "ADMIN"
    audit_action = "CREATE_ON_BEHALF"

    @staticmethod
    def payload(post, files):
        return {**CorrectionSubmissionService.payload(post, files),
                "faculty": post.get("faculty_user", ""),
                "on_behalf_reason": post.get("on_behalf_reason", "").strip()}

    @staticmethod
    def authorize(user, offering):
        from apps.academics.models import FacultyAssignment
        from apps.core.services.scope import ScopeService
        from apps.tenants.models import Department
        assert_not_denied(user, offering, ("admin_portal.access", "corrections.create_on_behalf"))
        for code in ("admin_portal.access", "corrections.create_on_behalf"):
            if not PermissionService.has_permission(user, code, offering.tenant_id, offering.campus_id):
                raise PermissionDenied("Administrative correction filing is not permitted in this scope.")
        # Match the existing offering/home-department scope, including global role rows.
        roles = PermissionService._scoped_user_roles(user, offering.tenant_id, offering.campus_id)
        non_faculty = roles.exclude(role__code="FACULTY")
        if non_faculty.exists():
            roles = non_faculty
        if user.is_superuser or roles.filter(department__isnull=True).exists():
            departments = list(Department.objects.filter(tenant_id=offering.tenant_id,
                campus_id=offering.campus_id, is_active=True).values_list("pk", flat=True))
        else:
            departments = ScopeService.expand_department_ids(roles.filter(department__is_active=True)
                .values_list("department_id", flat=True), tenant_id=offering.tenant_id, campus_id=offering.campus_id)
        if not (offering.is_active and offering.tenant.is_active and offering.campus.is_active) or not (
            offering.department_id in departments or FacultyAssignment.objects.filter(offering=offering,
                faculty_user__default_department_id__in=departments).exists()
        ):
            raise PermissionDenied("The course offering is outside your administrative department scope.")
