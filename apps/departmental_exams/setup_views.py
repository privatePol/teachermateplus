from django import forms
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .models import CycleCourse, ExaminationCycle
from .services import CourseExamConfigurationConflict, DepartmentalExamAuthorizationService
from .setup_services import CourseSetupService
from .views import _tenant_id, portal_required


class ClassificationForm(forms.Form):
    departmental_exam = forms.BooleanField(required=False, label="Departmental Exam")
    confirm_legacy_classification = forms.BooleanField(required=False, label="I confirm this legacy examination's classification")
    expected_state = forms.CharField(widget=forms.HiddenInput)


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def setup_view(request, cycle_id):
    cycle = get_object_or_404(ExaminationCycle, pk=cycle_id, tenant_id=_tenant_id(request))
    DepartmentalExamAuthorizationService.require_assigned_course_route_capability(user=request.user, tenant_id=cycle.tenant_id)
    if not cycle.cycle_courses.exists():
        DepartmentalExamAuthorizationService.require_automatic_tenant_permission(
            user=request.user, tenant_id=cycle.tenant_id,
            permission=DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION)
    rows = CourseSetupService.preview(cycle=cycle, actor=request.user)
    error, token, status, completed = "", "", 200, False
    if request.method == "POST":
        try:
            if request.POST.get("confirmation"):
                rows, reused = CourseSetupService.open_selection(cycle=cycle, actor=request.user, token=request.POST["confirmation"], request=request)
                completed = True
            else:
                try:
                    selected = {int(value) for value in request.POST.getlist("courses")}
                except ValueError as exc:
                    raise ValidationError("Select valid courses.") from exc
                if not selected:
                    raise ValidationError("Select at least one course.")
                rows = CourseSetupService.preview(cycle=cycle, actor=request.user, selected_ids=selected)
                if all(row["status"] in ("Ready", "Already open") for row in rows):
                    token = CourseSetupService.confirmation(cycle=cycle, actor=request.user, rows=rows)
                else:
                    error = "No courses opened. Remove blocked, exempt or historical courses before confirming."
        except (CourseExamConfigurationConflict, ValidationError) as exc:
            error = " ".join(exc.messages)
            status = 409 if isinstance(exc, CourseExamConfigurationConflict) else 400
            rows = CourseSetupService.preview(cycle=cycle, actor=request.user)
    return render(request, "departmental_exams/admin/course_setup.html", {"cycle": cycle, "rows": rows, "confirmation": token, "setup_error": error, "completed": completed}, status=status)


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def classification_view(request, cycle_course_id):
    course = get_object_or_404(CycleCourse.objects.select_related("cycle", "course"), pk=cycle_course_id, cycle__tenant_id=_tenant_id(request))
    DepartmentalExamAuthorizationService.require_configure_cycle_course(user=request.user, cycle_course=course)
    legacy = course.exam_classification == "UNCLASSIFIED_LEGACY"
    form = ClassificationForm(request.POST if request.method == "POST" else None, initial={"departmental_exam": course.exam_classification == "DEPARTMENTAL", "expected_state": CourseSetupService.fingerprint(course)})
    form.fields["confirm_legacy_classification"].required = legacy
    if not legacy:
        del form.fields["confirm_legacy_classification"]
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            CourseSetupService.classify(course_id=course.id, tenant_id=course.cycle.tenant_id, actor=request.user, classification="DEPARTMENTAL" if form.cleaned_data["departmental_exam"] else "STANDARDIZED", expected_state=form.cleaned_data["expected_state"], request=request)
        except (CourseExamConfigurationConflict, ValidationError) as exc:
            form.add_error(None, exc)
            status = 409 if isinstance(exc, CourseExamConfigurationConflict) else 400
        else:
            return redirect("departmental_exams:course_setup", cycle_id=course.cycle_id)
    return render(request, "departmental_exams/admin/course_classification.html", {"cycle_course": course, "form": form, "legacy": legacy}, status=status)
