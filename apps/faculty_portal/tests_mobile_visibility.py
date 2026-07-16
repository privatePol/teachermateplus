from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class FacultyMobileVisibilityTemplateTests(SimpleTestCase):
    @staticmethod
    def _template_source(relative_path):
        return (Path(settings.BASE_DIR) / "templates" / "faculty_portal" / relative_path).read_text(
            encoding="utf-8"
        )

    def test_base_collapses_utilities_and_hides_requested_mobile_navigation(self):
        source = self._template_source("base.html")

        self.assertIn("@media (max-width: 767.98px)", source)
        self.assertIn(".faculty-mobile-hidden", source)
        self.assertIn("faculty-utility-toggle", source)
        self.assertIn('aria-controls="faculty-utility-actions"', source)
        self.assertIn("faculty-utility-actions", source)
        self.assertIn("Help &amp; Privacy", source)
        self.assertIn("node.item.code == 'FACULTY_ANALYTICS'", source)
        self.assertIn('data-tour-id="performance-trends"', source)
        self.assertIn('data-tour-id="activity-history"', source)

    def test_dashboard_marks_requested_sections_as_mobile_hidden(self):
        source = self._template_source("dashboard.html")

        self.assertIn("dashboard-mobile-hidden-updates faculty-mobile-hidden", source)
        self.assertIn("dashboard-mobile-hidden-grade-status faculty-mobile-hidden", source)
        self.assertIn("dashboard-mobile-hidden-pending-issues faculty-mobile-hidden", source)
        self.assertIn(
            'class="faculty-mobile-hidden" href="{% url \'faculty_portal:parallel_section_comparison\' %}"',
            source,
        )

    def test_course_and_period_pages_hide_only_requested_mobile_cards(self):
        courses_source = self._template_source("my_courses.html")
        periods_source = self._template_source("offering_periods.html")

        self.assertIn("faculty-mobile-hidden my-courses-deadline-card", courses_source)
        self.assertIn("faculty-mobile-hidden period-template-summary-card", periods_source)
        self.assertIn("faculty-mobile-hidden period-deadline-card", periods_source)
        self.assertIn("period-guidance faculty-mobile-hidden", periods_source)
        self.assertIn("template-warning-banner", courses_source)
        self.assertIn("template-warning-note", courses_source)

    def test_grade_summary_has_dedicated_mobile_periodic_grade_table(self):
        source = self._template_source("period_summary.html")

        self.assertIn("desktop-class-record-table-wrap", source)
        self.assertIn("mobile-period-grade-table-wrap", source)
        self.assertIn("data-mobile-period-grade-table", source)
        self.assertIn('<th class="mobile-student-name-col">Student Name</th>', source)
        self.assertIn('<th class="mobile-period-grade-col">{{ period_grade_header_label }}</th>', source)
        self.assertIn('{{ row.period_grade|default:"" }}', source)
        self.assertIn("row.period_explain_url", source)

        mobile_table = source.split('data-mobile-period-grade-table>', 1)[1].split("</table>", 1)[0]
        self.assertNotIn("student.student_no", mobile_table)
        self.assertNotIn("class_standing_blocks", mobile_table)
        self.assertNotIn("exam_values", mobile_table)
        self.assertNotIn("row.final_grade", mobile_table)
