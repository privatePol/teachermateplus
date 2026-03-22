import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class StrongPasswordComplexityValidator:
    """
    Requires at least:
    - 1 uppercase letter
    - 1 lowercase letter
    - 1 digit
    - 1 special character
    """

    def validate(self, password, user=None):
        if not re.search(r"[A-Z]", password):
            raise ValidationError(_("Password must contain at least one uppercase letter."))
        if not re.search(r"[a-z]", password):
            raise ValidationError(_("Password must contain at least one lowercase letter."))
        if not re.search(r"\d", password):
            raise ValidationError(_("Password must contain at least one number."))
        if not re.search(r"[^A-Za-z0-9]", password):
            raise ValidationError(_("Password must contain at least one special character."))

    def get_help_text(self):
        return _(
            "Your password must contain at least one uppercase letter, one lowercase letter, one number, and one special character."
        )
