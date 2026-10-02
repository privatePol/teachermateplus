from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from apps.academics.models import CourseOffering
from apps.core.services.audit import AuditService

from .models import RecurringCombinedClass, RecurringCombinedClassOffering
from .permissions import MANAGE_MEETINGS_PERMISSION, require_attendance_permission
from .schedule_parsing import parse_schedule_text


def _current_faculty_ids(offering):
    assignments = [row for row in offering.faculty_assignments.all() if row.is_active]
    primary = [row for row in assignments if row.is_primary]
    return tuple(sorted(row.faculty_user_id for row in (primary or assignments)))


class RecurringCombinedClassService:
    @classmethod
    @transaction.atomic
    def create(cls, *, actor, tenant_id, campus_id, academic_year_id, term_id, offering_ids,
               weekday, start_time, end_time, effective_from, effective_until, reason):
        offering_ids = list(dict.fromkeys(int(value) for value in offering_ids))
        if len(offering_ids) < 2:
            raise ValidationError("Select at least two offerings taught together.")
        reason = (reason or "").strip()
        offerings = list(CourseOffering.objects.select_for_update().filter(pk__in=offering_ids).select_related(
            "course", "section", "department"
        ).prefetch_related("faculty_assignments__faculty_user"))
        if len(offerings) != len(offering_ids):
            raise ValidationError("One or more selected offerings are unavailable.")
        for offering in offerings:
            if (offering.tenant_id != tenant_id or offering.campus_id != campus_id or
                    offering.academic_year_id != academic_year_id or offering.term_id != term_id):
                raise ValidationError("All offerings must be in the selected tenant, campus, academic year, and semester.")
            require_attendance_permission(
                user=actor, permission_code=MANAGE_MEETINGS_PERMISSION, tenant_id=tenant_id,
                campus_id=campus_id, department_id=offering.department_id,
            )
            parsed = parse_schedule_text(offering.schedule_text)
            if not parsed.confirmed or not any(
                slot.weekday == weekday and slot.start_time == start_time and slot.end_time == end_time
                for slot in parsed.slots
            ):
                raise ValidationError(
                    f"{offering.course.code} / {offering.section.code} does not contain the exact selected recurring day and time."
                )
        rooms = {(offering.room or "").strip().casefold() for offering in offerings}
        if "" in rooms:
            raise ValidationError("Every selected offering needs a room before it can be confirmed as one shared class.")
        if len(rooms) != 1:
            raise ValidationError("Selected offerings have conflicting rooms; correct the academic offering evidence first.")
        faculty_sets = {_current_faculty_ids(offering) for offering in offerings}
        if () in faculty_sets:
            raise ValidationError("Every selected offering needs current faculty evidence before it can be confirmed as one shared class.")
        if len(faculty_sets) != 1:
            raise ValidationError("Selected offerings have conflicting faculty evidence; no faculty was chosen automatically.")
        overlap = RecurringCombinedClassOffering.objects.select_for_update().filter(
            offering_id__in=offering_ids,
            combined_class__weekday=weekday,
            combined_class__start_time=start_time,
            combined_class__end_time=end_time,
            combined_class__effective_from__lte=effective_until or date.max,
        ).filter(Q(combined_class__effective_until__isnull=True) | Q(combined_class__effective_until__gte=effective_from))
        if overlap.exists():
            raise ValidationError("An offering already belongs to an overlapping combined definition for this meeting pattern.")
        group = RecurringCombinedClass(
            tenant_id=tenant_id, campus_id=campus_id, academic_year_id=academic_year_id, term_id=term_id,
            weekday=weekday, start_time=start_time, end_time=end_time,
            effective_from=effective_from, effective_until=effective_until, reason=reason.strip(), created_by=actor,
        )
        group.full_clean()
        group.save()
        offering_map = {row.pk: row for row in offerings}
        for position, offering_id in enumerate(offering_ids):
            link = RecurringCombinedClassOffering(
                combined_class=group, offering=offering_map[offering_id], is_primary=position == 0,
            )
            link.full_clean()
            link.save()
        AuditService.log_event(
            action="FACULTY_ATTENDANCE_COMBINED_CLASS_CREATED", portal="ADMIN",
            entity_type="RecurringCombinedClass", entity_id=group.pk, actor=actor,
            tenant=tenant_id, campus=campus_id,
            after_data={"offering_ids": offering_ids, "weekday": weekday, "start_time": str(start_time),
                        "end_time": str(end_time), "effective_from": str(effective_from),
                        "effective_until": str(effective_until) if effective_until else None},
        )
        return group
