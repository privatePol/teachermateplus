from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.accounts.services import LoginLockoutService

User = get_user_model()


class PortalLoginForm(forms.Form):
    portal_code = ""
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    error_messages = {
        "invalid_login": "Invalid username or password.",
        "inactive": "This account is inactive.",
    }

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({"class": "form-control", "autofocus": "autofocus"})
        self.fields["password"].widget.attrs.update({"class": "form-control"})

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get("username")
        password = cleaned_data.get("password")
        if username and password:
            normalized_username = LoginLockoutService.normalize_username(username)
            cleaned_data["username"] = normalized_username
            lockout_status = LoginLockoutService.get_status(normalized_username, self.portal_code)
            if lockout_status.is_locked:
                LoginLockoutService.log_blocked_attempt(
                    username=normalized_username,
                    portal_code=self.portal_code,
                    request=self.request,
                )
                raise forms.ValidationError(LoginLockoutService.build_lockout_message(lockout_status.locked_until))

            matched_user = User.objects.filter(username__iexact=normalized_username).first()
            self.user_cache = authenticate(self.request, username=normalized_username, password=password)
            if self.user_cache is None:
                if matched_user and not matched_user.is_active:
                    raise forms.ValidationError(self.error_messages["inactive"])
                failure_status = LoginLockoutService.register_failure(
                    username=normalized_username,
                    portal_code=self.portal_code,
                    request=self.request,
                )
                if failure_status.is_locked:
                    raise forms.ValidationError(LoginLockoutService.build_lockout_message(failure_status.locked_until))
                raise forms.ValidationError(self.error_messages["invalid_login"])
            if not self.user_cache.is_active:
                raise forms.ValidationError(self.error_messages["inactive"])
        return cleaned_data

    def get_user(self):
        return self.user_cache


class AdminLoginForm(PortalLoginForm):
    portal_code = "ADMIN"


class FacultyLoginForm(PortalLoginForm):
    portal_code = "FACULTY"


class LoginOtpVerificationForm(forms.Form):
    otp_code = forms.CharField(
        label="Verification Code",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "placeholder": "Enter 6-digit code",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["otp_code"].widget.attrs.update({"class": "form-control text-center fw-bold"})

    def clean_otp_code(self):
        value = (self.cleaned_data.get("otp_code") or "").strip().replace(" ", "")
        if not value.isdigit():
            raise forms.ValidationError("Enter the 6-digit code sent to your registered email.")
        return value


class PortalForgotPasswordForm(forms.Form):
    identifier = forms.CharField(
        label="Username or Email",
        max_length=254,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "username",
                "placeholder": "Enter your username or email",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["identifier"].widget.attrs.update({"class": "form-control"})


class AdminForgotPasswordForm(PortalForgotPasswordForm):
    pass


class FacultyForgotPasswordForm(PortalForgotPasswordForm):
    pass


class PortalPasswordResetSetForm(SetPasswordForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})


class AdminPasswordResetSetForm(PortalPasswordResetSetForm):
    pass


class FacultyPasswordResetSetForm(PortalPasswordResetSetForm):
    pass


class FacultySelfChangePasswordForm(PasswordChangeForm):
    @staticmethod
    def _normalize(value):
        if value is None:
            return value
        # Remove common copy/paste artifacts from email clients.
        return str(value).replace("\u00a0", " ").strip()

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})
        self.fields["old_password"].widget.attrs.update({"autocomplete": "current-password"})
        self.fields["new_password1"].widget.attrs.update({"autocomplete": "new-password"})
        self.fields["new_password2"].widget.attrs.update({"autocomplete": "new-password"})
        self.fields["old_password"].help_text = "Tip: if pasted from email, make sure there are no leading/trailing spaces."

    def clean_old_password(self):
        self.cleaned_data["old_password"] = self._normalize(self.cleaned_data.get("old_password"))
        return super().clean_old_password()

    def clean_new_password1(self):
        value = self._normalize(self.cleaned_data.get("new_password1"))
        self.cleaned_data["new_password1"] = value
        return value

    def clean_new_password2(self):
        value = self._normalize(self.cleaned_data.get("new_password2"))
        self.cleaned_data["new_password2"] = value
        return value


class AdminSelfChangePasswordForm(PasswordChangeForm):
    @staticmethod
    def _normalize(value):
        if value is None:
            return value
        return str(value).replace("\u00a0", " ").strip()

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})
        self.fields["old_password"].widget.attrs.update({"autocomplete": "current-password"})
        self.fields["new_password1"].widget.attrs.update({"autocomplete": "new-password"})
        self.fields["new_password2"].widget.attrs.update({"autocomplete": "new-password"})
        self.fields["old_password"].help_text = "Tip: if pasted from email, make sure there are no leading/trailing spaces."

    def clean_old_password(self):
        self.cleaned_data["old_password"] = self._normalize(self.cleaned_data.get("old_password"))
        return super().clean_old_password()

    def clean_new_password1(self):
        value = self._normalize(self.cleaned_data.get("new_password1"))
        self.cleaned_data["new_password1"] = value
        return value

    def clean_new_password2(self):
        value = self._normalize(self.cleaned_data.get("new_password2"))
        self.cleaned_data["new_password2"] = value
        return value


class PrivacyConsentForm(forms.Form):
    CONFIRMATION_PHRASE = "I CONSENT"

    consent = forms.BooleanField(
        required=True,
        label="I have read and agree to the EduGradesPro Privacy Consent.",
    )
    confirmation_phrase = forms.CharField(
        required=True,
        label='Type "I CONSENT" to confirm.',
        help_text="Use the exact phrase shown. Do not type your name or other personal information.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["consent"].widget.attrs.update({"class": "form-check-input"})
        self.fields["confirmation_phrase"].widget.attrs.update({
            "class": "form-control",
            "autocomplete": "off",
            "placeholder": self.CONFIRMATION_PHRASE,
        })

    def clean_confirmation_phrase(self):
        value = (self.cleaned_data.get("confirmation_phrase") or "").strip()
        if value != self.CONFIRMATION_PHRASE:
            raise forms.ValidationError(f'Type "{self.CONFIRMATION_PHRASE}" exactly to continue.')
        return value


class UserSignatureUploadForm(forms.Form):
    signature_file = forms.FileField(
        label="Signature Image",
        help_text="Upload a PNG or JPG/JPEG file. EduGradesPro will normalize and encrypt it before storage.",
    )
    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        label="Current Password",
        help_text="Re-enter your current password to authorize signature upload or replacement.",
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["signature_file"].widget.attrs.update({"class": "form-control", "accept": ".png,.jpg,.jpeg,image/png,image/jpeg"})
        self.fields["current_password"].widget.attrs.update({"class": "form-control"})

    def clean_current_password(self):
        value = (self.cleaned_data.get("current_password") or "").strip()
        if not self.user.check_password(value):
            raise DjangoValidationError("Current password is incorrect.")
        return value


class UserSignatureDeleteForm(forms.Form):
    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        label="Current Password",
        help_text="Re-enter your current password to remove the stored signature.",
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["current_password"].widget.attrs.update({"class": "form-control"})

    def clean_current_password(self):
        value = (self.cleaned_data.get("current_password") or "").strip()
        if not self.user.check_password(value):
            raise DjangoValidationError("Current password is incorrect.")
        return value
