from __future__ import annotations

from django.db.models import Q

from apps.academics.models import AcademicYear, Term
from apps.core.services.settings import SystemSettingService


class AcademicGovernanceService:
    ACTIVE_AY_KEY = "ACTIVE_ACADEMIC_YEAR_CODE"
    ACTIVE_TERM_KEY = "ACTIVE_TERM_CODE"

    @classmethod
    def get_active_codes(cls, *, tenant_id: int):
        ay_code = (
            str(
                SystemSettingService.get(
                    cls.ACTIVE_AY_KEY,
                    tenant_id=tenant_id,
                    default="",
                )
                or ""
            )
            .strip()
            .upper()
        )
        term_code = (
            str(
                SystemSettingService.get(
                    cls.ACTIVE_TERM_KEY,
                    tenant_id=tenant_id,
                    default="",
                )
                or ""
            )
            .strip()
            .upper()
        )
        return ay_code, term_code

    @classmethod
    def resolve_active_scope(cls, *, tenant_id: int):
        ay_code, term_code = cls.get_active_codes(tenant_id=tenant_id)
        if not ay_code or not term_code:
            return None, None

        academic_year = (
            AcademicYear.objects.filter(tenant_id=tenant_id, is_active=True)
            .filter(Q(code__iexact=ay_code) | Q(name__iexact=ay_code))
            .order_by("-start_date", "-id")
            .first()
        )
        if not academic_year:
            return None, None

        term = (
            Term.objects.filter(
                tenant_id=tenant_id,
                academic_year_id=academic_year.id,
                is_active=True,
            )
            .filter(Q(code__iexact=term_code) | Q(name__iexact=term_code))
            .order_by("sequence_no", "id")
            .first()
        )
        if not term:
            return academic_year, None
        return academic_year, term

    @classmethod
    def set_active_scope(
        cls,
        *,
        tenant_id: int,
        academic_year: AcademicYear | None,
        term: Term | None,
    ):
        if academic_year and term and term.academic_year_id != academic_year.id:
            raise ValueError("Active term must belong to the selected academic year.")

        if academic_year is None or term is None:
            SystemSettingService.set(
                cls.ACTIVE_AY_KEY,
                "",
                tenant_id=tenant_id,
                value_type="STRING",
                is_active=False,
            )
            SystemSettingService.set(
                cls.ACTIVE_TERM_KEY,
                "",
                tenant_id=tenant_id,
                value_type="STRING",
                is_active=False,
            )
            return

        SystemSettingService.set(
            cls.ACTIVE_AY_KEY,
            academic_year.code,
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            cls.ACTIVE_TERM_KEY,
            term.code,
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
