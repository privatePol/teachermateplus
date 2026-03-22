from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm


class PortalLoginForm(forms.Form):
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
            self.user_cache = authenticate(self.request, username=username, password=password)
            if self.user_cache is None:
                raise forms.ValidationError(self.error_messages["invalid_login"])
            if not self.user_cache.is_active:
                raise forms.ValidationError(self.error_messages["inactive"])
        return cleaned_data

    def get_user(self):
        return self.user_cache


class AdminLoginForm(PortalLoginForm):
    pass


class FacultyLoginForm(PortalLoginForm):
    pass


class FacultyForgotPasswordForm(forms.Form):
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


class FacultyPasswordResetSetForm(SetPasswordForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})


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
    consent = forms.BooleanField(
        required=True,
        label="I have read and agree to the EduGradesPro Data Privacy Policy.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["consent"].widget.attrs.update({"class": "form-check-input"})
