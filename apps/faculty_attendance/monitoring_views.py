"""Scoped staff monitoring endpoints. GET never creates notices or attendance records."""

from django import forms
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.academics.models import AcademicYear, Term
from apps.core.decorators import portal_required
from apps.tenants.models import Campus

from .monitoring import authorized_departments, term_summary
from .staff_notices import current_notices


class TermMonitoringForm(forms.Form):
    academic_year = forms.ModelChoiceField(queryset=AcademicYear.objects.none(), label='Academic year')
    term = forms.ModelChoiceField(queryset=Term.objects.none(), label='Semester')
    as_of = forms.DateField(label='As of date', widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, tenant_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['academic_year'].queryset = AcademicYear.objects.filter(tenant_id=tenant_id)
        self.fields['term'].queryset = Term.objects.filter(tenant_id=tenant_id)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean(self):
        values = super().clean()
        year, term, as_of = values.get('academic_year'), values.get('term'), values.get('as_of')
        if year and term and term.academic_year_id != year.pk:
            self.add_error('term', 'Select a semester within the chosen academic year.')
        if term and (not term.start_date or not term.end_date):
            self.add_error('term', 'Set the academic semester dates before monitoring scheduled hours.')
        if as_of and as_of > timezone.localdate():
            self.add_error('as_of', 'Choose today or an earlier date; future classes are not hours to date.')
        return values


@portal_required('ADMIN')
@require_GET
def term_monitoring_view(request, faculty_id=None):
    scope = getattr(request, 'scope', {})
    tenant_id, campus_id = scope.get('tenant_id'), scope.get('campus_id')
    if not tenant_id or not campus_id:
        return HttpResponseForbidden('Select an authorized tenant and campus.')
    try:
        authorized_departments(actor=request.user, tenant_id=tenant_id, campus_id=campus_id)
    except PermissionDenied:
        return HttpResponseForbidden('Faculty Attendance monitoring is unavailable in this scope.')
    data = request.GET.copy() if request.GET else None
    if data is not None and not data.get('as_of'):
        data['as_of'] = timezone.localdate().isoformat()
    form = TermMonitoringForm(data, tenant_id=tenant_id, initial={'as_of': timezone.localdate()})
    report = None
    selected = None
    if form.is_bound and form.is_valid():
        try:
            report = term_summary(actor=request.user, tenant_id=tenant_id, campus_id=campus_id,
                                  **form.cleaned_data)
        except PermissionDenied:
            return HttpResponseForbidden('Academic scope is not available for this account.')
        if faculty_id is not None:
            selected = next((row for row in report['rows'] if row['faculty'] and row['faculty'].pk == faculty_id), None)
            if selected is None:
                return HttpResponseForbidden('Faculty details are not available in this authorized academic scope.')
    elif faculty_id is not None:
        return HttpResponseForbidden('Select a valid authorized academic scope before viewing faculty details.')
    notices = current_notices(actor=request.user, tenant_id=tenant_id, campus_id=campus_id)
    context = {
        'form': form, 'report': report, 'selected': selected, 'notices': notices,
        'campus': Campus.objects.get(pk=campus_id, tenant_id=tenant_id),
        'academic_year': form.cleaned_data.get('academic_year') if form.is_bound and form.is_valid() else None,
        'term': form.cleaned_data.get('term') if form.is_bound and form.is_valid() else None,
    }
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        valid = not form.is_bound or form.is_valid()
        return JsonResponse({
            'ok': valid,
            'html': render_to_string('faculty_attendance/_term_monitoring_content.html', context, request=request),
            'message': ('Faculty details loaded.' if selected else 'Summary loaded.') if valid else
                       'Check the highlighted filters and try again.',
            'has_details': selected is not None,
        }, status=200 if valid else 400)
    return render(request, 'faculty_attendance/term_monitoring.html', context)
