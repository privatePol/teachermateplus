from __future__ import annotations

from apps.core.services.settings import SystemSettingService


class FeatureSettingsService:
    CORRECTION_OFFICIAL_REPORT_ENABLED_KEY = "FEATURE_CORRECTION_OFFICIAL_REPORT_ENABLED"
    CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY = "FEATURE_CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED"
    CORRECTION_SUBMISSION_APPROVAL_EMAIL_ROLE_CODES_KEY = "FEATURE_CORRECTION_SUBMISSION_APPROVAL_EMAIL_ROLE_CODES"
    CORRECTION_REGISTRAR_AUTO_EMAIL_ENABLED_KEY = "FEATURE_CORRECTION_REGISTRAR_AUTO_EMAIL_ENABLED"
    CORRECTION_REGISTRAR_AUTO_EMAIL_ROLE_CODES_KEY = "FEATURE_CORRECTION_REGISTRAR_AUTO_EMAIL_ROLE_CODES"
    CORRECTION_REGISTRAR_DEFAULT_RECIPIENTS_KEY = "FEATURE_CORRECTION_REGISTRAR_DEFAULT_RECIPIENTS"
    CORRECTION_REGISTRAR_CAMPUS_RECIPIENTS_KEY = "FEATURE_CORRECTION_REGISTRAR_CAMPUS_RECIPIENTS"
    FACULTY_ASSIGNMENT_REMINDERS_ENABLED_KEY = "FEATURE_FACULTY_ASSIGNMENT_REMINDERS_ENABLED"
    FACULTY_ASSIGNMENT_AUTO_EXPIRE_ENABLED_KEY = "FEATURE_FACULTY_ASSIGNMENT_AUTO_EXPIRE_ENABLED"
    FACULTY_ASSIGNMENT_PRIMARY_DEFAULT_ENABLED_KEY = "FEATURE_FACULTY_ASSIGNMENT_PRIMARY_DEFAULT_ENABLED"
    FACULTY_ASSIGNMENT_RESPONSE_WINDOW_DAYS_KEY = "FEATURE_FACULTY_ASSIGNMENT_RESPONSE_WINDOW_DAYS"
    FACULTY_ASSIGNMENT_FIRST_REMINDER_DAYS_KEY = "FEATURE_FACULTY_ASSIGNMENT_FIRST_REMINDER_DAYS"
    FACULTY_ASSIGNMENT_REPEAT_REMINDER_DAYS_KEY = "FEATURE_FACULTY_ASSIGNMENT_REPEAT_REMINDER_DAYS"
    FACULTY_REMINDER_CENTER_ENABLED_KEY = "FEATURE_FACULTY_REMINDER_CENTER_ENABLED"
    FACULTY_REMINDER_EMAIL_ENABLED_KEY = "FEATURE_FACULTY_REMINDER_EMAIL_ENABLED"
    FACULTY_MEMO_CENTER_ENABLED_KEY = "FEATURE_FACULTY_MEMO_CENTER_ENABLED"
    FACULTY_QUICK_TOUR_ENABLED_KEY = "FEATURE_FACULTY_QUICK_TOUR_ENABLED"
    FACULTY_QUICK_SCORE_ENCODING_KEY = "FEATURE_FACULTY_QUICK_SCORE_ENCODING"
    LOGIN_LOCKOUT_ENABLED_KEY = "FEATURE_LOGIN_LOCKOUT_ENABLED"
    LOGIN_LOCKOUT_MAX_ATTEMPTS_KEY = "FEATURE_LOGIN_LOCKOUT_MAX_ATTEMPTS"
    LOGIN_LOCKOUT_WINDOW_MINUTES_KEY = "FEATURE_LOGIN_LOCKOUT_WINDOW_MINUTES"
    LOGIN_LOCKOUT_DURATION_MINUTES_KEY = "FEATURE_LOGIN_LOCKOUT_DURATION_MINUTES"
    LOGIN_EMAIL_OTP_ENABLED_KEY = "FEATURE_LOGIN_EMAIL_OTP_ENABLED"
    LOGIN_EMAIL_OTP_EXPIRY_MINUTES_KEY = "FEATURE_LOGIN_EMAIL_OTP_EXPIRY_MINUTES"
    SINGLE_DEVICE_SESSION_ENFORCEMENT_ENABLED_KEY = "FEATURE_SINGLE_DEVICE_SESSION_ENFORCEMENT_ENABLED"
    SESSION_TIMEOUT_MINUTES_KEY = "FEATURE_SESSION_TIMEOUT_MINUTES"
    GRADE_PREDICTION_ENABLED_KEY = "FEATURE_GRADE_PREDICTION_ENABLED"
    GRADE_PREDICTION_ROLE_CODES_KEY = "FEATURE_GRADE_PREDICTION_ROLE_CODES"
    GRADE_PREDICTION_WHAT_IF_ENABLED_KEY = "FEATURE_GRADE_PREDICTION_WHAT_IF_ENABLED"
    GRADE_PREDICTION_WHAT_IF_ROLE_CODES_KEY = "FEATURE_GRADE_PREDICTION_WHAT_IF_ROLE_CODES"
    GRADE_PREDICTION_AT_RISK_ENABLED_KEY = "FEATURE_GRADE_PREDICTION_AT_RISK_ENABLED"
    GRADE_PREDICTION_SHOW_BEST_CASE_KEY = "FEATURE_GRADE_PREDICTION_SHOW_BEST_CASE"
    GRADE_PREDICTION_SHOW_WORST_CASE_KEY = "FEATURE_GRADE_PREDICTION_SHOW_WORST_CASE"
    GRADE_PREDICTION_SHOW_TARGET_NEEDED_KEY = "FEATURE_GRADE_PREDICTION_SHOW_TARGET_NEEDED"
    GRADE_PREDICTION_DEFAULT_ASSUMPTION_KEY = "FEATURE_GRADE_PREDICTION_DEFAULT_ASSUMPTION"
    FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_DEADLINE_KEY = "FEATURE_FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_DEADLINE"
    FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_SUBMISSION_KEY = "FEATURE_FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_SUBMISSION"
    FACULTY_OFFICIAL_FINAL_GRADES_AFTER_DEADLINE_KEY = "FEATURE_FACULTY_OFFICIAL_FINAL_GRADES_AFTER_DEADLINE"
    USER_SIGNATURES_ENABLED_KEY = "FEATURE_USER_SIGNATURES_ENABLED"
    USER_SIGNATURES_FINAL_CLEARANCE_ENABLED_KEY = "FEATURE_USER_SIGNATURES_FINAL_CLEARANCE_ENABLED"
    USER_SIGNATURES_CORRECTION_REPORT_ENABLED_KEY = "FEATURE_USER_SIGNATURES_CORRECTION_REPORT_ENABLED"
    SUBMISSION_NON_COMPLIANCE_NOTICE_ENABLED_KEY = "FEATURE_SUBMISSION_NON_COMPLIANCE_NOTICE_ENABLED"
    SUBMISSION_NON_COMPLIANCE_NOTICE_INTERVAL_DAYS_KEY = "FEATURE_SUBMISSION_NON_COMPLIANCE_NOTICE_INTERVAL_DAYS"
    SUBMISSION_NON_COMPLIANCE_FIRST_NOTICE_AFTER_DAYS_KEY = "FEATURE_SUBMISSION_NON_COMPLIANCE_FIRST_NOTICE_AFTER_DAYS"
    SUBMISSION_NON_COMPLIANCE_LEVEL_INTERVAL_DAYS_KEY = "FEATURE_SUBMISSION_NON_COMPLIANCE_LEVEL_INTERVAL_DAYS"
    SUBMISSION_NON_COMPLIANCE_MAX_NOTICE_COUNT_KEY = "FEATURE_SUBMISSION_NON_COMPLIANCE_MAX_NOTICE_COUNT"
    SUBMISSION_NON_COMPLIANCE_HEAD_ROLE_CODES_KEY = "FEATURE_SUBMISSION_NON_COMPLIANCE_HEAD_ROLE_CODES"
    SUBMISSION_NON_COMPLIANCE_HR_RECIPIENTS_KEY = "FEATURE_SUBMISSION_NON_COMPLIANCE_HR_RECIPIENTS"
    GRADE_DEADLINE_ENFORCEMENT_POLICY_KEY = "FEATURE_GRADE_DEADLINE_ENFORCEMENT_POLICY"
    STUDENT_PORTAL_ENABLED_KEY = "FEATURE_STUDENT_PORTAL_ENABLED"
    STUDENT_PORTAL_PERIOD_GRADES_AFTER_SUBMISSION_KEY = "FEATURE_STUDENT_PORTAL_PERIOD_GRADES_AFTER_SUBMISSION"
    STUDENT_PORTAL_FINAL_GRADES_AFTER_SUBMISSION_KEY = "FEATURE_STUDENT_PORTAL_FINAL_GRADES_AFTER_SUBMISSION"
    STUDENT_PORTAL_ATTENDANCE_DETAILS_ENABLED_KEY = "FEATURE_STUDENT_PORTAL_ATTENDANCE_DETAILS_ENABLED"
    SIS_PERIODIC_GRADES_API_ENABLED_KEY = "FEATURE_SIS_PERIODIC_GRADES_API_ENABLED"
    ROLE_BASED_HELP_GUIDE_ENABLED_KEY = "FEATURE_ROLE_BASED_HELP_GUIDE_ENABLED"
    ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED_KEY = "FEATURE_ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED"
    GRADE_DEADLINE_POLICY_COMPLIANCE_ONLY = "COMPLIANCE_ONLY"
    GRADE_DEADLINE_POLICY_DISABLED = "DISABLED"
    GRADE_DEADLINE_POLICY_AUTO_CLOSE_REQUIRES_REOPEN = "AUTO_CLOSE_REQUIRES_REOPEN"

    @staticmethod
    def _positive_int(value, *, default: int, minimum: int = 0) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(parsed, minimum)

    @staticmethod
    def _role_code_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip().upper() for item in value if str(item).strip()]

    @staticmethod
    def _user_role_codes(user) -> set[str]:
        if getattr(user, "is_superuser", False):
            return {"SUPER_ADMIN"}
        return {
            str(code).strip().upper()
            for code in user.user_roles.filter(is_active=True, role__is_active=True).values_list("role__code", flat=True)
            if str(code).strip()
        }

    @classmethod
    def is_correction_official_report_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.CORRECTION_OFFICIAL_REPORT_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_correction_submission_approval_email_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_correction_submission_approval_email_role_codes(
        cls,
        *,
        tenant_id: int | None,
        default: list[str] | None = None,
    ) -> list[str]:
        value = SystemSettingService.get(
            cls.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ROLE_CODES_KEY,
            tenant_id=tenant_id,
            default=default or [],
        )
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @classmethod
    def is_correction_registrar_auto_email_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.CORRECTION_REGISTRAR_AUTO_EMAIL_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_correction_registrar_auto_email_role_codes(
        cls,
        *,
        tenant_id: int | None,
        default: list[str] | None = None,
    ) -> list[str]:
        value = SystemSettingService.get(
            cls.CORRECTION_REGISTRAR_AUTO_EMAIL_ROLE_CODES_KEY,
            tenant_id=tenant_id,
            default=default or [],
        )
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @classmethod
    def get_correction_registrar_default_recipients(
        cls,
        *,
        tenant_id: int | None,
        default: list[str] | None = None,
    ) -> list[str]:
        value = SystemSettingService.get(
            cls.CORRECTION_REGISTRAR_DEFAULT_RECIPIENTS_KEY,
            tenant_id=tenant_id,
            default=default or [],
        )
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @classmethod
    def get_correction_registrar_campus_recipients(
        cls,
        *,
        tenant_id: int | None,
        default: dict[str, list[str]] | None = None,
    ) -> dict[str, list[str]]:
        value = SystemSettingService.get(
            cls.CORRECTION_REGISTRAR_CAMPUS_RECIPIENTS_KEY,
            tenant_id=tenant_id,
            default=default or {},
        )
        if not isinstance(value, dict):
            return {}
        normalized = {}
        for campus_id, recipients in value.items():
            if isinstance(recipients, list):
                emails = [str(item).strip() for item in recipients if str(item).strip()]
            else:
                emails = []
            normalized[str(campus_id)] = emails
        return normalized

    @classmethod
    def is_faculty_assignment_reminders_enabled(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.FACULTY_ASSIGNMENT_REMINDERS_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_faculty_assignment_auto_expire_enabled(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.FACULTY_ASSIGNMENT_AUTO_EXPIRE_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_faculty_assignment_primary_default_enabled(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.FACULTY_ASSIGNMENT_PRIMARY_DEFAULT_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_faculty_reminder_center_enabled(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.FACULTY_REMINDER_CENTER_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_faculty_reminder_email_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.FACULTY_REMINDER_EMAIL_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_faculty_memo_center_enabled(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.FACULTY_MEMO_CENTER_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_faculty_quick_tour_enabled(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.FACULTY_QUICK_TOUR_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_faculty_quick_score_encoding_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.FACULTY_QUICK_SCORE_ENCODING_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_login_lockout_enabled(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.LOGIN_LOCKOUT_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_login_lockout_max_attempts(cls, *, tenant_id: int | None, default: int = 5) -> int:
        return cls._positive_int(
            SystemSettingService.get(
                cls.LOGIN_LOCKOUT_MAX_ATTEMPTS_KEY,
                tenant_id=tenant_id,
                default=default,
            ),
            default=default,
            minimum=1,
        )

    @classmethod
    def get_login_lockout_window_minutes(cls, *, tenant_id: int | None, default: int = 15) -> int:
        return cls._positive_int(
            SystemSettingService.get(
                cls.LOGIN_LOCKOUT_WINDOW_MINUTES_KEY,
                tenant_id=tenant_id,
                default=default,
            ),
            default=default,
            minimum=1,
        )

    @classmethod
    def get_login_lockout_duration_minutes(cls, *, tenant_id: int | None, default: int = 15) -> int:
        return cls._positive_int(
            SystemSettingService.get(
                cls.LOGIN_LOCKOUT_DURATION_MINUTES_KEY,
                tenant_id=tenant_id,
                default=default,
            ),
            default=default,
            minimum=1,
        )

    @classmethod
    def is_login_email_otp_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.LOGIN_EMAIL_OTP_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_login_email_otp_expiry_minutes(cls, *, tenant_id: int | None, default: int = 10) -> int:
        return cls._positive_int(
            SystemSettingService.get(
                cls.LOGIN_EMAIL_OTP_EXPIRY_MINUTES_KEY,
                tenant_id=tenant_id,
                default=default,
            ),
            default=default,
            minimum=1,
        )

    @classmethod
    def is_single_device_session_enforcement_enabled(
        cls,
        *,
        tenant_id: int | None,
        default: bool = True,
    ) -> bool:
        return bool(
            SystemSettingService.get(
                cls.SINGLE_DEVICE_SESSION_ENFORCEMENT_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_session_timeout_minutes(cls, *, tenant_id: int | None, default: int = 60) -> int:
        return cls._positive_int(
            SystemSettingService.get(
                cls.SESSION_TIMEOUT_MINUTES_KEY,
                tenant_id=tenant_id,
                default=default,
            ),
            default=default,
            minimum=1,
        )

    @classmethod
    def is_student_portal_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.STUDENT_PORTAL_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def show_student_portal_period_grades_after_submission(
        cls,
        *,
        tenant_id: int | None,
        default: bool = True,
    ) -> bool:
        return bool(
            SystemSettingService.get(
                cls.STUDENT_PORTAL_PERIOD_GRADES_AFTER_SUBMISSION_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def show_student_portal_final_grades_after_submission(
        cls,
        *,
        tenant_id: int | None,
        default: bool = True,
    ) -> bool:
        return bool(
            SystemSettingService.get(
                cls.STUDENT_PORTAL_FINAL_GRADES_AFTER_SUBMISSION_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def show_student_portal_attendance_details(
        cls,
        *,
        tenant_id: int | None,
        default: bool = True,
    ) -> bool:
        return bool(
            SystemSettingService.get(
                cls.STUDENT_PORTAL_ATTENDANCE_DETAILS_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_sis_periodic_grades_api_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.SIS_PERIODIC_GRADES_API_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_faculty_assignment_response_window_days(cls, *, tenant_id: int | None, default: int = 3) -> int:
        return cls._positive_int(
            SystemSettingService.get(
                cls.FACULTY_ASSIGNMENT_RESPONSE_WINDOW_DAYS_KEY,
                tenant_id=tenant_id,
                default=default,
            ),
            default=default,
            minimum=1,
        )

    @classmethod
    def get_faculty_assignment_first_reminder_days(cls, *, tenant_id: int | None, default: int = 1) -> int:
        return cls._positive_int(
            SystemSettingService.get(
                cls.FACULTY_ASSIGNMENT_FIRST_REMINDER_DAYS_KEY,
                tenant_id=tenant_id,
                default=default,
            ),
            default=default,
            minimum=0,
        )

    @classmethod
    def get_faculty_assignment_repeat_reminder_days(cls, *, tenant_id: int | None, default: int = 1) -> int:
        return cls._positive_int(
            SystemSettingService.get(
                cls.FACULTY_ASSIGNMENT_REPEAT_REMINDER_DAYS_KEY,
                tenant_id=tenant_id,
                default=default,
            ),
            default=default,
            minimum=1,
        )

    @classmethod
    def is_grade_prediction_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.GRADE_PREDICTION_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_grade_prediction_role_codes(
        cls,
        *,
        tenant_id: int | None,
        default: list[str] | None = None,
    ) -> list[str]:
        value = SystemSettingService.get(
            cls.GRADE_PREDICTION_ROLE_CODES_KEY,
            tenant_id=tenant_id,
            default=default
            or ["FACULTY", "DEAN", "COLLEGE_DEAN", "REGISTRAR", "CAMPUS_ADMIN", "TENANT_ADMIN", "SUPER_ADMIN"],
        )
        return cls._role_code_list(value)

    @classmethod
    def is_grade_prediction_what_if_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.GRADE_PREDICTION_WHAT_IF_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_grade_prediction_what_if_role_codes(
        cls,
        *,
        tenant_id: int | None,
        default: list[str] | None = None,
    ) -> list[str]:
        value = SystemSettingService.get(
            cls.GRADE_PREDICTION_WHAT_IF_ROLE_CODES_KEY,
            tenant_id=tenant_id,
            default=default or ["FACULTY", "SUPER_ADMIN"],
        )
        return cls._role_code_list(value)

    @classmethod
    def is_grade_prediction_at_risk_enabled(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.GRADE_PREDICTION_AT_RISK_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def show_grade_prediction_best_case(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.GRADE_PREDICTION_SHOW_BEST_CASE_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def show_grade_prediction_worst_case(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.GRADE_PREDICTION_SHOW_WORST_CASE_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def show_grade_prediction_target_needed(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.GRADE_PREDICTION_SHOW_TARGET_NEEDED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_grade_prediction_default_assumption(
        cls,
        *,
        tenant_id: int | None,
        default: str = "IGNORE_MISSING",
    ) -> str:
        value = str(
            SystemSettingService.get(
                cls.GRADE_PREDICTION_DEFAULT_ASSUMPTION_KEY,
                tenant_id=tenant_id,
                default=default,
            )
            or default
        ).strip().upper()
        if value not in {"IGNORE_MISSING", "RAW_ZERO", "FULL_SCORE"}:
            return default
        return value

    @classmethod
    def show_faculty_official_period_grades_after_deadline(
        cls,
        *,
        tenant_id: int | None,
        default: bool = False,
    ) -> bool:
        return bool(
            SystemSettingService.get(
                cls.FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_DEADLINE_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def show_faculty_official_period_grades_after_submission(
        cls,
        *,
        tenant_id: int | None,
        default: bool = False,
    ) -> bool:
        return bool(
            SystemSettingService.get(
                cls.FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_SUBMISSION_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def show_faculty_official_final_grades_after_deadline(
        cls,
        *,
        tenant_id: int | None,
        default: bool = False,
    ) -> bool:
        return bool(
            SystemSettingService.get(
                cls.FACULTY_OFFICIAL_FINAL_GRADES_AFTER_DEADLINE_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_user_signatures_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.USER_SIGNATURES_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_user_signature_final_clearance_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.USER_SIGNATURES_FINAL_CLEARANCE_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_user_signature_correction_report_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.USER_SIGNATURES_CORRECTION_REPORT_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_submission_non_compliance_notice_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.SUBMISSION_NON_COMPLIANCE_NOTICE_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_submission_non_compliance_notice_interval_days(
        cls,
        *,
        tenant_id: int | None,
        default: int = 1,
    ) -> int:
        # The scheduler is fixed to a daily check; the notice service caps the
        # current NCBA policy at Day 1, Day 2, and Day 3.
        return 1

    @classmethod
    def get_submission_non_compliance_first_notice_after_days(
        cls,
        *,
        tenant_id: int | None,
        default: int = 1,
    ) -> int:
        value = SystemSettingService.get(
            cls.SUBMISSION_NON_COMPLIANCE_FIRST_NOTICE_AFTER_DAYS_KEY,
            tenant_id=tenant_id,
            default=default,
        )
        return cls._positive_int(value, default=default, minimum=1)

    @classmethod
    def get_submission_non_compliance_level_interval_days(
        cls,
        *,
        tenant_id: int | None,
        default: int = 1,
    ) -> int:
        value = SystemSettingService.get(
            cls.SUBMISSION_NON_COMPLIANCE_LEVEL_INTERVAL_DAYS_KEY,
            tenant_id=tenant_id,
            default=default,
        )
        return cls._positive_int(value, default=default, minimum=1)

    @classmethod
    def get_submission_non_compliance_max_notice_count(
        cls,
        *,
        tenant_id: int | None,
        default: int = 3,
    ) -> int:
        value = SystemSettingService.get(
            cls.SUBMISSION_NON_COMPLIANCE_MAX_NOTICE_COUNT_KEY,
            tenant_id=tenant_id,
            default=default,
        )
        return min(cls._positive_int(value, default=default, minimum=1), 3)

    @classmethod
    def get_submission_non_compliance_head_role_codes(
        cls,
        *,
        tenant_id: int | None,
        default: list[str] | None = None,
    ) -> list[str]:
        value = SystemSettingService.get(
            cls.SUBMISSION_NON_COMPLIANCE_HEAD_ROLE_CODES_KEY,
            tenant_id=tenant_id,
            default=default or ["CAO", "DEAN", "COLLEGE_DEAN", "AC", "AREA_CHAIR", "AREA_CHAIRPERSON"],
        )
        return cls._role_code_list(value)

    @classmethod
    def get_submission_non_compliance_hr_recipients(
        cls,
        *,
        tenant_id: int | None,
        default: list[str] | None = None,
    ) -> list[str]:
        value = SystemSettingService.get(
            cls.SUBMISSION_NON_COMPLIANCE_HR_RECIPIENTS_KEY,
            tenant_id=tenant_id,
            default=default or [],
        )
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @classmethod
    def get_grade_deadline_enforcement_policy(
        cls,
        *,
        tenant_id: int | None,
        default: str = GRADE_DEADLINE_POLICY_AUTO_CLOSE_REQUIRES_REOPEN,
    ) -> str:
        value = str(
            SystemSettingService.get(
                cls.GRADE_DEADLINE_ENFORCEMENT_POLICY_KEY,
                tenant_id=tenant_id,
                default=default,
            )
            or default
        ).strip().upper()
        if value == cls.GRADE_DEADLINE_POLICY_COMPLIANCE_ONLY:
            return cls.GRADE_DEADLINE_POLICY_AUTO_CLOSE_REQUIRES_REOPEN
        allowed = {
            cls.GRADE_DEADLINE_POLICY_DISABLED,
            cls.GRADE_DEADLINE_POLICY_AUTO_CLOSE_REQUIRES_REOPEN,
        }
        return value if value in allowed else default

    @classmethod
    def is_grade_deadline_auto_close_enabled(cls, *, tenant_id: int | None) -> bool:
        return cls.get_grade_deadline_enforcement_policy(
            tenant_id=tenant_id
        ) == cls.GRADE_DEADLINE_POLICY_AUTO_CLOSE_REQUIRES_REOPEN

    @classmethod
    def is_role_based_help_guide_enabled(cls, *, tenant_id: int | None, default: bool = True) -> bool:
        return bool(
            SystemSettingService.get(
                cls.ROLE_BASED_HELP_GUIDE_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_academic_performance_insights_enabled(
        cls,
        *,
        tenant_id: int | None,
        default: bool = False,
    ) -> bool:
        return bool(
            SystemSettingService.get(
                cls.ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def can_user_access_grade_prediction(cls, *, user, tenant_id: int | None) -> bool:
        if not cls.is_grade_prediction_enabled(tenant_id=tenant_id):
            return False
        allowed_role_codes = set(cls.get_grade_prediction_role_codes(tenant_id=tenant_id))
        if not allowed_role_codes:
            return True
        return bool(cls._user_role_codes(user) & allowed_role_codes)

    @classmethod
    def can_user_access_grade_prediction_what_if(cls, *, user, tenant_id: int | None) -> bool:
        if not cls.can_user_access_grade_prediction(user=user, tenant_id=tenant_id):
            return False
        if not cls.is_grade_prediction_what_if_enabled(tenant_id=tenant_id):
            return False
        allowed_role_codes = set(cls.get_grade_prediction_what_if_role_codes(tenant_id=tenant_id))
        if not allowed_role_codes:
            return True
        return bool(cls._user_role_codes(user) & allowed_role_codes)
