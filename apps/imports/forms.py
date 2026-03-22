from django import forms


class ImportUploadForm(forms.Form):
    csv_file = forms.FileField(help_text="Upload CSV generated from the official template.")

    def clean_csv_file(self):
        file_obj = self.cleaned_data["csv_file"]
        name = (file_obj.name or "").lower()
        if not name.endswith(".csv"):
            raise forms.ValidationError("Only .csv files are allowed.")
        return file_obj

