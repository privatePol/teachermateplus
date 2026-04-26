from django import forms

from apps.core.services.uploads import UploadValidationService


class ImportUploadForm(forms.Form):
    csv_file = forms.FileField(help_text="Upload CSV generated from the official template.")

    def clean_csv_file(self):
        file_obj = self.cleaned_data["csv_file"]
        self.cleaned_data["csv_file_validation"] = UploadValidationService.validate_import_csv(file_obj)
        return file_obj
