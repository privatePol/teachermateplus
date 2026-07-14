from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import FacultyInvitation
from apps.core.services.audit import AuditService
from apps.core.services.email_assets import format_email_subject
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Role, UserRole
from apps.tenants.models import Campus, Department, Tenant

User = get_user_model()


@dataclass(frozen=True)
class RoleAssignmentResult:
    assignment: UserRole
    created: bool
    reactivated: bool


@dataclass(frozen=True)
class FacultyProvisioningResult:
    user: User
    role_assignment: UserRole
    user_created: bool
    role_created: bool


@dataclass(frozen=True)
class FacultyInvitationDeliveryResult:
    invitation: FacultyInvitation
    sent: bool
    resent: bool
    error_code: str | None = None


class ScopedUserRoleAssignmentService:
    @staticmethod
    def _validate_scope(*, actor, tenant: Tenant | None, campus: Campus | None, department: Department | None):
        if campus and tenant and campus.tenant_id != tenant.id:
            raise ValidationError("Selected campus does not belong to the selected tenant.")
        if department and tenant and department.tenant_id != tenant.id:
            raise ValidationError("Selected department does not belong to the selected tenant.")
        if department and campus and department.campus_id != campus.id:
            raise ValidationError("Selected department does not belong to the selected campus.")
        if department and not campus:
            raise ValidationError("Select a campus when assigning a department-scoped role.")
        if tenant and not tenant.is_active:
            raise ValidationError("Selected tenant is inactive.")
        if campus and not campus.is_active:
            raise ValidationError("Selected campus is inactive.")
        if department and not department.is_active:
            raise ValidationError("Selected department is inactive.")

        if getattr(actor, "is_superuser", False):
            return
        if tenant and tenant.id not in ScopeService.get_accessible_tenant_ids(actor):
            raise PermissionDenied("Selected tenant is outside your scope.")
        if campus and campus.id not in ScopeService.get_accessible_campus_ids(actor, tenant_id=tenant.id if tenant else None):
            raise PermissionDenied("Selected campus is outside your scope.")
        if department and department.id not in ScopeService.get_accessible_department_ids(
            actor,
            tenant_id=tenant.id if tenant else None,
            campus_id=campus.id if campus else None,
        ):
            raise PermissionDenied("Selected department is outside your scope.")

    @classmethod
    def assign(
        cls,
        *,
        actor,
        user,
        role: Role,
        tenant: Tenant | None,
        campus: Campus | None,
        department: Department | None,
        permission_code: str,
        request=None,
        import_batch_id=None,
        import_row_number=None,
    ) -> RoleAssignmentResult:
        if not role.is_active:
            raise ValidationError("Selected role is inactive.")
        if not PermissionService.has_permission(
            actor,
            permission_code,
            tenant_id=tenant.id if tenant else None,
            campus_id=campus.id if campus else None,
        ):
            raise PermissionDenied("You do not have permission to assign this role.")
        cls._validate_scope(actor=actor, tenant=tenant, campus=campus, department=department)

        assignment, created = UserRole.objects.get_or_create(
            user=user,
            role=role,
            tenant=tenant,
            campus=campus,
            department=department,
            defaults={"is_active": True},
        )
        reactivated = False
        if not created and not assignment.is_active:
            assignment.is_active = True
            assignment.save(update_fields=["is_active"])
            reactivated = True

        AuditService.log_event(
            action="FACULTY_ROLE_ASSIGNED" if role.code == "FACULTY" else ("CREATE" if created else "UPDATE"),
            portal="ADMIN",
            entity_type="UserRole",
            entity_id=assignment.id,
            actor=actor,
            tenant=tenant,
            campus=campus,
            after_data={
                "user_id": user.id,
                "role_code": role.code,
                "tenant_id": tenant.id if tenant else None,
                "campus_id": campus.id if campus else None,
                "department_id": department.id if department else None,
                "created": created,
                "reactivated": reactivated,
            },
            metadata={
                "import_batch_id": import_batch_id,
                "import_row_number": import_row_number,
            },
            request=request,
        )
        return RoleAssignmentResult(assignment=assignment, created=created, reactivated=reactivated)


class FacultyAccountProvisioningService:
    IMPORT_PERMISSION = "faculty_users.import"
    FACULTY_ROLE_CODE = "FACULTY"

    @staticmethod
    def normalize_username(username: str) -> str:
        return str(username or "").strip().lower()

    @staticmethod
    def normalize_email(email: str) -> str:
        return str(email or "").strip().lower()

    @staticmethod
    def allowed_email_domains(tenant_id: int | None) -> list[str]:
        raw_value = SystemSettingService.get(
            "USER_EMAIL_ALLOWED_DOMAINS",
            tenant_id=tenant_id,
            default="ncba.edu.ph",
        )
        if isinstance(raw_value, list):
            raw_value = ",".join(str(value) for value in raw_value)
        domains = [part.strip().lower() for part in str(raw_value or "").replace(";", ",").split(",")]
        return [domain for domain in domains if domain] or ["ncba.edu.ph"]

    @classmethod
    def validate_email_for_tenant(cls, email: str, tenant_id: int | None) -> str:
        normalized = cls.normalize_email(email)
        validate_email(normalized)
        domain = normalized.rsplit("@", 1)[-1]
        allowed = cls.allowed_email_domains(tenant_id)
        if domain not in allowed:
            raise ValidationError(
                f"Email domain '{domain}' is not allowed. Allowed domain(s): {', '.join(allowed)}."
            )
        return normalized

    @classmethod
    def resolve_active_faculty_role(cls) -> Role:
        role = Role.objects.filter(code=cls.FACULTY_ROLE_CODE, is_active=True).first()
        if not role:
            raise ValidationError("The active FACULTY role is not configured.")
        return role

    @classmethod
    def provision(
        cls,
        *,
        actor,
        tenant: Tenant,
        campus: Campus,
        department: Department,
        first_name: str,
        middle_name: str,
        last_name: str,
        email: str,
        username: str,
        request=None,
        import_batch_id=None,
        import_row_number=None,
    ) -> FacultyProvisioningResult:
        if not PermissionService.has_permission(
            actor,
            cls.IMPORT_PERMISSION,
            tenant_id=tenant.id,
            campus_id=campus.id,
        ):
            raise PermissionDenied("You do not have permission to import faculty users in this scope.")
        ScopedUserRoleAssignmentService._validate_scope(
            actor=actor,
            tenant=tenant,
            campus=campus,
            department=department,
        )
        role = cls.resolve_active_faculty_role()
        normalized_email = cls.validate_email_for_tenant(email, tenant.id)
        normalized_username = cls.normalize_username(username)
        if not normalized_username:
            raise ValidationError("Username is required.")
        if User.objects.filter(email__iexact=normalized_email).exists():
            raise ValidationError("Email is already assigned to another user.")
        if User.objects.filter(username__iexact=normalized_username).exists():
            raise ValidationError("Username is already assigned to another user.")

        with transaction.atomic():
            user = User.objects.create_user(
                username=normalized_username,
                email=normalized_email,
                password=None,
                first_name=str(first_name or "").strip(),
                middle_name=str(middle_name or "").strip() or None,
                last_name=str(last_name or "").strip(),
                default_tenant=tenant,
                default_campus=campus,
                default_department=department,
                is_active=False,
                is_staff=False,
                must_change_password=False,
            )
            AuditService.log_event(
                action="FACULTY_USER_CREATED",
                portal="ADMIN",
                entity_type="User",
                entity_id=user.id,
                actor=actor,
                tenant=tenant,
                campus=campus,
                after_data={
                    "username": user.username,
                    "email": user.email,
                    "default_tenant_id": tenant.id,
                    "default_campus_id": campus.id,
                    "default_department_id": department.id,
                    "is_active": False,
                    "has_usable_password": False,
                },
                metadata={
                    "department_id": department.id,
                    "import_batch_id": import_batch_id,
                    "import_row_number": import_row_number,
                },
                request=request,
            )
            role_result = ScopedUserRoleAssignmentService.assign(
                actor=actor,
                user=user,
                role=role,
                tenant=tenant,
                campus=campus,
                department=department,
                permission_code=cls.IMPORT_PERMISSION,
                request=request,
                import_batch_id=import_batch_id,
                import_row_number=import_row_number,
            )
        return FacultyProvisioningResult(
            user=user,
            role_assignment=role_result.assignment,
            user_created=True,
            role_created=role_result.created,
        )


class FacultyInvitationService:
    TOKEN_SALT = "teachermateplus.faculty-invitation.v1"
    RESEND_THROTTLE = timedelta(minutes=5)

    @staticmethod
    def expiry_hours() -> int:
        return max(1, int(getattr(settings, "FACULTY_INVITATION_EXPIRY_HOURS", 24)))

    @classmethod
    def _token(cls, invitation: FacultyInvitation) -> str:
        return signing.dumps(
            {"invitation": invitation.public_id.hex, "version": invitation.version},
            salt=cls.TOKEN_SALT,
            compress=True,
        )

    @classmethod
    def _setup_url(cls, *, request, invitation: FacultyInvitation, token: str) -> str:
        path = reverse(
            "accounts:faculty_invitation_accept",
            kwargs={"public_id": invitation.public_id},
        )
        if request is not None:
            base_url = request.build_absolute_uri(path)
        else:
            base_url = f"{str(getattr(settings, 'SITE_URL', '') or '').rstrip('/')}{path}"
        # URL fragments are not sent in HTTP requests, so the signed token is absent
        # from normal application and reverse-proxy access logs.
        return f"{base_url}#{token}"

    @staticmethod
    def _scope_for_user(user):
        return user.default_tenant_id, user.default_campus_id

    @classmethod
    def record_without_delivery(cls, *, user, actor, originating_import_row, status: str):
        invitation, _ = FacultyInvitation.objects.get_or_create(
            user=user,
            defaults={
                "originating_import_row": originating_import_row,
                "status": status,
                "created_by_user": actor,
            },
        )
        if invitation.status != FacultyInvitation.Status.ACCEPTED:
            invitation.originating_import_row = originating_import_row or invitation.originating_import_row
            invitation.status = status
            invitation.failure_reason = None
            invitation.save(
                update_fields=["originating_import_row", "status", "failure_reason", "updated_at"]
            )
        return invitation

    @classmethod
    def send_or_resend(
        cls,
        *,
        user,
        actor,
        originating_import_row=None,
        request=None,
        resend: bool,
    ) -> FacultyInvitationDeliveryResult:
        if not getattr(settings, "FACULTY_IMPORT_EMAIL_ENABLED", False):
            raise ValidationError("Faculty invitation email is disabled for this environment.")
        permission_code = (
            "faculty_users.resend_invitation" if resend else "faculty_users.send_import_invitations"
        )
        tenant_id, campus_id = cls._scope_for_user(user)
        if not PermissionService.has_permission(
            actor,
            permission_code,
            tenant_id=tenant_id,
            campus_id=campus_id,
        ):
            raise PermissionDenied("You do not have permission to send this faculty invitation.")
        if user.is_active and user.has_usable_password():
            raise ValidationError("This faculty account is already login-ready.")
        if not UserRole.objects.filter(
            user=user,
            role__code="FACULTY",
            role__is_active=True,
            is_active=True,
            tenant_id=tenant_id,
            campus_id=campus_id,
            department_id=user.default_department_id,
        ).exists():
            raise ValidationError("The user does not have the required active scoped FACULTY role.")

        now = timezone.now()
        with transaction.atomic():
            invitation, created = FacultyInvitation.objects.select_for_update().get_or_create(
                user=user,
                defaults={
                    "originating_import_row": originating_import_row,
                    "created_by_user": actor,
                },
            )
            if invitation.status == FacultyInvitation.Status.ACCEPTED:
                raise ValidationError("This invitation has already been accepted.")
            if invitation.last_attempted_at and now - invitation.last_attempted_at < cls.RESEND_THROTTLE:
                raise ValidationError("Wait at least five minutes before sending another invitation.")
            was_previously_attempted = invitation.attempt_count > 0
            if was_previously_attempted:
                invitation.superseded_at = now
            invitation.version += 1
            invitation.attempt_count += 1
            invitation.last_attempted_at = now
            invitation.originating_import_row = originating_import_row or invitation.originating_import_row
            invitation.last_resend_by_user = actor if (resend or was_previously_attempted) else None
            invitation.status = FacultyInvitation.Status.NOT_REQUESTED
            invitation.failure_reason = None
            invitation.save()

        token = cls._token(invitation)
        setup_url = cls._setup_url(request=request, invitation=invitation, token=token)
        context = {
            "user": user,
            "setup_url": setup_url,
            "expiry_hours": cls.expiry_hours(),
        }
        text_body = render_to_string("accounts/emails/faculty_invitation.txt", context)
        html_body = render_to_string("accounts/emails/faculty_invitation.html", context)
        message = EmailMultiAlternatives(
            subject=format_email_subject("Faculty Account Invitation"),
            body=text_body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@teachermateplus.local"),
            to=[user.email],
        )
        message.attach_alternative(html_body, "text/html")

        try:
            sent_count = message.send(fail_silently=False)
            if sent_count <= 0:
                raise RuntimeError("smtp_accepted_zero_messages")
        except Exception as exc:
            invitation.status = FacultyInvitation.Status.FAILED
            invitation.failure_reason = type(exc).__name__[:255]
            invitation.expires_at = None
            invitation.save(update_fields=["status", "failure_reason", "expires_at", "updated_at"])
            AuditService.log_event(
                action="FACULTY_INVITATION_FAILED",
                portal="ADMIN",
                entity_type="FacultyInvitation",
                entity_id=invitation.id,
                actor=actor,
                tenant=tenant_id,
                campus=campus_id,
                metadata={
                    "user_id": user.id,
                    "attempt_count": invitation.attempt_count,
                    "error_type": type(exc).__name__,
                    "import_batch_id": getattr(originating_import_row, "batch_id", None),
                    "import_row_number": getattr(originating_import_row, "row_number", None),
                },
                request=request,
            )
            return FacultyInvitationDeliveryResult(
                invitation=invitation,
                sent=False,
                resent=was_previously_attempted,
                error_code=type(exc).__name__,
            )

        sent_at = timezone.now()
        invitation.status = FacultyInvitation.Status.SENT
        invitation.last_successfully_sent_at = sent_at
        invitation.expires_at = sent_at + timedelta(hours=cls.expiry_hours())
        invitation.failure_reason = None
        invitation.save(
            update_fields=[
                "status",
                "last_successfully_sent_at",
                "expires_at",
                "failure_reason",
                "updated_at",
            ]
        )
        AuditService.log_event(
            action="FACULTY_INVITATION_RESENT" if was_previously_attempted else "FACULTY_INVITATION_SENT",
            portal="ADMIN",
            entity_type="FacultyInvitation",
            entity_id=invitation.id,
            actor=actor,
            tenant=tenant_id,
            campus=campus_id,
            metadata={
                "user_id": user.id,
                "attempt_count": invitation.attempt_count,
                "expires_at": invitation.expires_at,
                "import_batch_id": getattr(originating_import_row, "batch_id", None),
                "import_row_number": getattr(originating_import_row, "row_number", None),
            },
            request=request,
        )
        return FacultyInvitationDeliveryResult(
            invitation=invitation,
            sent=True,
            resent=was_previously_attempted,
        )

    @classmethod
    def resolve_valid(cls, *, public_id, token: str, for_update: bool = False) -> FacultyInvitation | None:
        try:
            payload = signing.loads(
                token,
                salt=cls.TOKEN_SALT,
            )
        except signing.BadSignature:
            return None
        queryset = FacultyInvitation.objects.select_related("user")
        if for_update:
            queryset = queryset.select_for_update()
        invitation = queryset.filter(public_id=public_id).first()
        if not invitation:
            return None
        if payload.get("invitation") != invitation.public_id.hex or payload.get("version") != invitation.version:
            return None
        if invitation.status != FacultyInvitation.Status.SENT:
            return None
        if not invitation.last_successfully_sent_at or not invitation.expires_at:
            return None
        if invitation.expires_at <= timezone.now():
            invitation.status = FacultyInvitation.Status.EXPIRED
            invitation.save(update_fields=["status", "updated_at"])
            return None
        if invitation.user.is_active and invitation.user.has_usable_password():
            return None
        return invitation

    @classmethod
    def get_open_invitation(cls, *, public_id) -> FacultyInvitation | None:
        invitation = FacultyInvitation.objects.select_related("user").filter(public_id=public_id).first()
        if not invitation or invitation.status != FacultyInvitation.Status.SENT:
            return None
        if not invitation.expires_at or invitation.expires_at <= timezone.now():
            invitation.status = FacultyInvitation.Status.EXPIRED
            invitation.save(update_fields=["status", "updated_at"])
            return None
        if invitation.user.is_active and invitation.user.has_usable_password():
            return None
        return invitation

    @classmethod
    def mark_accepted(cls, *, invitation: FacultyInvitation, request=None):
        now = timezone.now()
        user = invitation.user
        user.is_active = True
        user.must_change_password = False
        user.save(update_fields=["is_active", "must_change_password", "updated_at"])
        invitation.status = FacultyInvitation.Status.ACCEPTED
        invitation.accepted_at = now
        invitation.expires_at = None
        invitation.failure_reason = None
        invitation.save(
            update_fields=["status", "accepted_at", "expires_at", "failure_reason", "updated_at"]
        )
        AuditService.log_event(
            action="FACULTY_INVITATION_ACCEPTED",
            portal="FACULTY",
            entity_type="FacultyInvitation",
            entity_id=invitation.id,
            actor=None,
            tenant=user.default_tenant_id,
            campus=user.default_campus_id,
            metadata={"user_id": user.id, "department_id": user.default_department_id},
            request=request,
        )
        return user
