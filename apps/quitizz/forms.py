from django import forms

from .models import QuiTizz, QuiTizzQuestion


class StyledForm:
    def style(self):
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"


class QuiTizzForm(StyledForm, forms.ModelForm):
    class Meta:
        model = QuiTizz
        fields = ["title"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()


class QuestionForm(StyledForm, forms.ModelForm):
    class Meta:
        model = QuiTizzQuestion
        fields = ["prompt", "choice_a", "choice_b", "choice_c", "choice_d", "correct_choice", "timer_seconds"]
        widgets = {name: forms.Textarea(attrs={"rows": 3 if name == "prompt" else 2}) for name in ["prompt", "choice_a", "choice_b", "choice_c", "choice_d"]}
        labels = {"prompt": "Question prompt", "choice_a": "Choice A", "choice_b": "Choice B", "choice_c": "Choice C", "choice_d": "Choice D", "correct_choice": "Correct answer", "timer_seconds": "Timer (seconds)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()


class LaunchPresentationForm(StyledForm, forms.Form):
    show_fastest = forms.BooleanField(required=False, initial=True, label="Show Fastest Five")
    show_scores = forms.BooleanField(required=False, initial=True, label="Show Highest Scores")
    final_fastest = forms.BooleanField(required=False, initial=False, label="Show final Fastest Correct Award")
    answer_seconds = forms.IntegerField(min_value=1, max_value=60, initial=2, label="Correct Answer seconds")
    fastest_seconds = forms.IntegerField(min_value=1, max_value=60, initial=5, label="Fastest Five seconds")
    scores_seconds = forms.IntegerField(min_value=1, max_value=60, initial=5, label="Highest Scores seconds")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style()
        for name in ("show_fastest", "show_scores", "final_fastest"):
            self.fields[name].widget.attrs["class"] = "form-check-input"
