from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.services.audit import AuditService

from .models import SavedCheckerRoute, SavedCheckerRouteEntry, ScheduleSlot, TeachingMeeting
from .permissions import MANAGE_ROUTES_PERMISSION, require_attendance_permission


def default_meeting_order(queryset):
    return queryset.order_by(
        "meeting_date",
        "starts_at",
        "schedule_slot__building",
        "schedule_slot__floor",
        "schedule_slot__room",
        "pk",
    )


def ordered_meetings(queryset, route=None):
    meetings = list(default_meeting_order(queryset).select_related("schedule_slot"))
    if not route:
        return meetings
    positions = dict(route.entries.values_list("schedule_slot_id", "position"))
    fallback = len(positions) + 1
    return sorted(
        meetings,
        key=lambda item: (
            item.meeting_date,
            positions.get(item.schedule_slot_id, fallback),
            item.starts_at,
            item.schedule_slot.building,
            item.schedule_slot.floor,
            item.schedule_slot.room,
            item.pk,
        ),
    )


class SavedRouteService:
    @classmethod
    @transaction.atomic
    def save(cls, *, actor, tenant_id, campus_id, name, schedule_slot_ids, expected_revision=None, route=None):
        ordered_ids = [int(value) for value in schedule_slot_ids]
        if not ordered_ids:
            raise ValidationError("A saved route requires at least one structured schedule entry.")
        if len(ordered_ids) != len(set(ordered_ids)):
            raise ValidationError("Route order contains duplicate schedule entries.")
        if route:
            route = SavedCheckerRoute.objects.select_for_update().get(pk=route.pk, owner=actor)
            if route.tenant_id != tenant_id or route.campus_id != campus_id:
                raise ValidationError("Saved route is outside the active scope.")
            if expected_revision != route.revision:
                raise ValidationError("Saved route changed; reload before saving your order.")
            retained_ids = list(route.entries.order_by("position").values_list("schedule_slot_id", flat=True))
            ordered_ids.extend(slot_id for slot_id in retained_ids if slot_id not in ordered_ids)
        slots = list(
            ScheduleSlot.objects.select_for_update()
            .select_related("schedule_version__offering")
            .filter(pk__in=ordered_ids)
        )
        if len(slots) != len(ordered_ids):
            raise ValidationError("One or more route entries are stale or unavailable.")
        if any(
            row.schedule_version.offering.tenant_id != tenant_id
            or row.schedule_version.offering.campus_id != campus_id
            for row in slots
        ):
            raise ValidationError("Route entries must remain inside the authorized tenant and campus.")
        for department_id in {row.schedule_version.offering.department_id for row in slots}:
            require_attendance_permission(
                user=actor,
                permission_code=MANAGE_ROUTES_PERMISSION,
                tenant_id=tenant_id,
                campus_id=campus_id,
                department_id=department_id,
            )
        if route:
            route.name = name.strip()
            route.revision += 1
        else:
            route = SavedCheckerRoute(tenant_id=tenant_id, campus_id=campus_id, owner=actor, name=name.strip())
        if not route.name:
            raise ValidationError("Route name is required.")
        route.full_clean()
        route.save()
        route.entries.all().delete()
        slot_map = {row.pk: row for row in slots}
        for position, slot_id in enumerate(ordered_ids, start=1):
            entry = SavedCheckerRouteEntry(route=route, schedule_slot=slot_map[slot_id], position=position)
            entry.full_clean()
            entry.save()
        AuditService.log_event(
            action="FACULTY_ATTENDANCE_ROUTE_SAVED",
            portal="ADMIN",
            entity_type="SavedCheckerRoute",
            entity_id=route.pk,
            actor=actor,
            tenant=tenant_id,
            campus=campus_id,
            after_data={"revision": route.revision, "entry_count": len(ordered_ids)},
        )
        return route

    @classmethod
    @transaction.atomic
    def reset(cls, *, actor, route, expected_revision):
        route = SavedCheckerRoute.objects.select_for_update().get(pk=route.pk, owner=actor)
        department_ids = set(
            route.entries.values_list("schedule_slot__schedule_version__offering__department_id", flat=True)
        ) or {getattr(actor, "default_department_id", None)}
        for department_id in department_ids:
            require_attendance_permission(
                user=actor,
                permission_code=MANAGE_ROUTES_PERMISSION,
                tenant_id=route.tenant_id,
                campus_id=route.campus_id,
                department_id=department_id,
            )
        if route.revision != expected_revision:
            raise ValidationError("Saved route changed; reload before resetting.")
        route.entries.all().delete()
        route.revision += 1
        route.save(update_fields=["revision", "updated_at"])
        return route
