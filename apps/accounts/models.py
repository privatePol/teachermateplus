from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, username, email, password, **extra_fields):
        if not username:
            raise ValueError("The given username must be set")
        email = self.normalize_email(email)
        username = username.strip().lower()
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(username, email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    middle_name = models.CharField(max_length=150, blank=True, null=True)
    default_tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.SET_NULL,
        related_name="default_users",
        blank=True,
        null=True,
    )
    default_campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.SET_NULL,
        related_name="default_users",
        blank=True,
        null=True,
    )
    default_department = models.ForeignKey(
        "tenants.Department",
        on_delete=models.SET_NULL,
        related_name="default_users",
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)
    faculty_quick_tour_disabled = models.BooleanField(default=False)
    privacy_consent_version = models.CharField(max_length=32, blank=True, null=True)
    privacy_consent_at = models.DateTimeField(blank=True, null=True)
    privacy_consent_ip = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        db_table = "users"
        ordering = ["username"]

    def __str__(self):
        return self.username

    @property
    def full_name(self):
        name = " ".join(part for part in [self.first_name, self.middle_name, self.last_name] if part)
        return name.strip() or self.username


class UserSignatureCredential(models.Model):
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="signature_credential",
    )
    encrypted_blob = models.BinaryField(blank=True, null=True)
    encryption_nonce = models.BinaryField(blank=True, null=True)
    original_filename = models.CharField(max_length=255, blank=True, null=True)
    mime_type = models.CharField(max_length=100, blank=True, null=True)
    image_format = models.CharField(max_length=20, blank=True, null=True)
    image_width = models.PositiveIntegerField(blank=True, null=True)
    image_height = models.PositiveIntegerField(blank=True, null=True)
    file_size_bytes = models.PositiveIntegerField(default=0)
    content_sha256 = models.CharField(max_length=64, blank=True, null=True)
    uploaded_at = models.DateTimeField(blank=True, null=True)
    uploaded_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="uploaded_signature_credentials",
    )
    last_used_at = models.DateTimeField(blank=True, null=True)
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_signature_credentials"
        ordering = ["user__username"]

    def __str__(self):
        return f"signature:{self.user.username}"

    @property
    def has_signature(self):
        return bool(self.encrypted_blob and self.encryption_nonce and self.is_enabled)


class UserSignatureUsageLog(models.Model):
    class DocumentType(models.TextChoices):
        FINAL_CLEARANCE = "FINAL_CLEARANCE", "Faculty Final Clearance"
        CORRECTION_OFFICIAL_REPORT = "CORRECTION_OFFICIAL_REPORT", "Correction Official Report"

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="signature_usage_logs",
    )
    document_type = models.CharField(max_length=40, choices=DocumentType.choices)
    document_reference = models.CharField(max_length=128)
    usage_role = models.CharField(max_length=100, blank=True, null=True)
    portal_code = models.CharField(max_length=20, blank=True, null=True)
    actor = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="performed_signature_usage_logs",
    )
    used_at = models.DateTimeField(auto_now_add=True)
    metadata_json = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = "user_signature_usage_logs"
        ordering = ["-used_at"]
        indexes = [
            models.Index(fields=["document_type", "document_reference"]),
            models.Index(fields=["user", "used_at"]),
        ]

    def __str__(self):
        return f"{self.document_type}:{self.document_reference}:{self.user.username}"


class PortalLoginLockoutState(models.Model):
    class PortalCode(models.TextChoices):
        ADMIN = "ADMIN", "Admin Portal"
        FACULTY = "FACULTY", "Faculty Portal"

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="login_lockout_states",
        blank=True,
        null=True,
    )
    username = models.CharField(max_length=150)
    portal_code = models.CharField(max_length=20, choices=PortalCode.choices)
    failed_attempt_count = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField(blank=True, null=True)
    last_failed_at = models.DateTimeField(blank=True, null=True)
    locked_until = models.DateTimeField(blank=True, null=True)
    last_ip = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "portal_login_lockout_states"
        ordering = ["portal_code", "username"]
        constraints = [
            models.UniqueConstraint(
                fields=["username", "portal_code"],
                name="uniq_portal_login_lockout_username_portal",
            )
        ]
        indexes = [
            models.Index(fields=["portal_code", "username"]),
            models.Index(fields=["portal_code", "locked_until"]),
        ]

    def __str__(self):
        return f"{self.portal_code}:{self.username}"


class LoginOtpChallenge(models.Model):
    class PortalCode(models.TextChoices):
        ADMIN = "ADMIN", "Admin Portal"
        FACULTY = "FACULTY", "Faculty Portal"

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="login_otp_challenges",
    )
    portal_code = models.CharField(max_length=20, choices=PortalCode.choices)
    code_hash = models.CharField(max_length=128)
    sent_to_email = models.EmailField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(blank=True, null=True)
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "login_otp_challenges"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "portal_code", "created_at"]),
            models.Index(fields=["portal_code", "expires_at"]),
        ]

    def __str__(self):
        return f"{self.portal_code}:{self.user_id}:{self.created_at:%Y%m%d%H%M%S}"

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    @property
    def is_consumed(self):
        return self.consumed_at is not None
