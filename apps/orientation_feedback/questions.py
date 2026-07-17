from __future__ import annotations

from apps.orientation_feedback.models import OrientationSurveyQuestion, OrientationSurveySession


def _scale(code, section, section_title, text, scale_kind, *, order, reverse=False, index=""):
    return {
        "code": code,
        "section_code": section,
        "section_title": section_title,
        "text": text,
        "question_type": OrientationSurveyQuestion.QuestionType.SCALE,
        "scale_kind": scale_kind,
        "is_required": True,
        "reverse_scored": reverse,
        "composite_index_code": index,
        "display_order": order,
    }


def _multi(code, section, section_title, text, options, *, order):
    return {
        "code": code,
        "section_code": section,
        "section_title": section_title,
        "text": text,
        "question_type": OrientationSurveyQuestion.QuestionType.MULTI_SELECT,
        "scale_kind": "",
        "is_required": True,
        "reverse_scored": False,
        "composite_index_code": "",
        "display_order": order,
        "options": options,
    }


def _text(code, section, section_title, text, *, order):
    return {
        "code": code,
        "section_code": section,
        "section_title": section_title,
        "text": text,
        "question_type": OrientationSurveyQuestion.QuestionType.TEXT,
        "scale_kind": "",
        "is_required": False,
        "reverse_scored": False,
        "composite_index_code": "",
        "display_order": order,
    }


SCALE_CHOICES = {
    OrientationSurveyQuestion.ScaleKind.QUALITY: [
        ("5", "Excellent", "🤩", 5),
        ("4", "Good", "😊", 4),
        ("3", "Fair", "🙂", 3),
        ("2", "Needs Improvement", "😕", 2),
        ("1", "Poor", "😞", 1),
    ],
    OrientationSurveyQuestion.ScaleKind.EASE: [
        ("5", "Very Easy", "🤩", 5),
        ("4", "Easy", "😊", 4),
        ("3", "Neutral", "😐", 3),
        ("2", "Difficult", "😕", 2),
        ("1", "Very Difficult", "😞", 1),
    ],
    OrientationSurveyQuestion.ScaleKind.CLARITY: [
        ("5", "Very Clear", "🤩", 5),
        ("4", "Clear", "😊", 4),
        ("3", "Neutral", "😐", 3),
        ("2", "Unclear", "😕", 2),
        ("1", "Very Unclear", "😞", 1),
    ],
    OrientationSurveyQuestion.ScaleKind.PACE: [
        ("5", "Very Appropriate", "🤩", 5),
        ("4", "Appropriate", "😊", 4),
        ("3", "Neutral", "😐", 3),
        ("2", "Needs Adjustment", "😕", 2),
        ("1", "Not Appropriate", "😞", 1),
    ],
    OrientationSurveyQuestion.ScaleKind.CONFIDENCE: [
        ("5", "Very Confident", "🤩", 5),
        ("4", "Confident", "😊", 4),
        ("3", "Somewhat Confident", "🙂", 3),
        ("2", "Not Yet Confident", "😕", 2),
        ("1", "Not Confident", "😞", 1),
    ],
    OrientationSurveyQuestion.ScaleKind.READINESS: [
        ("5", "Yes, I am ready", "🤩", 5),
        ("4", "Mostly ready", "😊", 4),
        ("3", "I still need a little practice", "🙂", 3),
        ("2", "I need additional guidance", "😕", 2),
        ("1", "I am not yet ready", "😞", 1),
    ],
    OrientationSurveyQuestion.ScaleKind.AGREEMENT: [
        ("5", "Strongly Agree", "❤️", 5),
        ("4", "Agree", "😊", 4),
        ("3", "Neutral", "😐", 3),
        ("2", "Disagree", "😕", 2),
        ("1", "Strongly Disagree", "💔", 1),
    ],
}


FACULTY_GUIDANCE_OPTIONS = [
    "Logging in and accessing the portal",
    "Viewing assigned classes",
    "Encoding attendance",
    "Creating activities",
    "Encoding scores or grades",
    "Submitting grades",
    "Viewing reports",
    "None at the moment",
    "Others",
]

HEADS_GUIDANCE_OPTIONS = [
    "Dashboard and reports",
    "Faculty assignment management",
    "Student enrollment management",
    "Grade encoding controls",
    "Monitoring faculty compliance",
    "Reviewing academic reports",
    "User account management",
    "System configuration",
    "Correction and approval workflows",
    "None at the moment",
    "Others",
]


def _option_rows(labels):
    rows = []
    for index, label in enumerate(labels, start=1):
        rows.append(
            {
                "code": f"OPTION_{index}",
                "label": label,
                "emoji": "",
                "score": None,
                "display_order": index,
                "allows_other_text": label == "Others",
            }
        )
    return rows


def faculty_questions():
    experience = "A"
    experience_title = "Orientation Experience"
    technology = "C"
    technology_title = "Technology and Work Preferences"
    return [
        _scale("overall_rating", experience, experience_title, "Overall, how would you rate the TeacherMate+ Faculty Portal orientation?", OrientationSurveyQuestion.ScaleKind.QUALITY, order=1),
        _scale("ease_follow", experience, experience_title, "How easy was it to follow the orientation?", OrientationSurveyQuestion.ScaleKind.EASE, order=2, index="ORIENTATION_CLARITY"),
        _scale("demo_clarity", experience, experience_title, "How clear and easy to understand was the demonstration?", OrientationSurveyQuestion.ScaleKind.CLARITY, order=3, index="ORIENTATION_CLARITY"),
        _scale("pace", experience, experience_title, "Was the pace of the orientation appropriate?", OrientationSurveyQuestion.ScaleKind.PACE, order=4),
        _scale("confidence", experience, experience_title, "After the orientation, how confident are you in using the TeacherMate+ Faculty Portal?", OrientationSurveyQuestion.ScaleKind.CONFIDENCE, order=5, index="READINESS_TO_USE"),
        _scale("readiness", experience, experience_title, "Do you feel ready to start using TeacherMate+ for your classes?", OrientationSurveyQuestion.ScaleKind.READINESS, order=6, index="READINESS_TO_USE"),
        _multi("guidance_areas", "B", "Areas Needing More Guidance", "Which areas would you like additional guidance on?", _option_rows(FACULTY_GUIDANCE_OPTIONS), order=7),
        _scale("manual_records_preference", technology, technology_title, "I prefer recording grades and class records manually rather than using an online system.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=8, reverse=True, index="TECHNOLOGY_OPENNESS"),
        _scale("flexibility_preference", technology, technology_title, "I prefer having flexibility in managing my class records and grading processes.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=9),
        _scale("technology_comfort", technology, technology_title, "I am comfortable using technology as part of my teaching responsibilities.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=10, index="TECHNOLOGY_OPENNESS"),
        _scale("open_to_teachermate", technology, technology_title, "I am open to using TeacherMate+ if it helps make my work easier and more organized.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=11, index="TECHNOLOGY_OPENNESS"),
        _scale("standardization_value", technology, technology_title, "A standardized grading system helps promote consistency and fairness.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=12, index="TECHNOLOGY_OPENNESS"),
        _scale("paperwork_reduction", technology, technology_title, "TeacherMate+ can reduce paperwork and repetitive administrative tasks.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=13, index="TECHNOLOGY_OPENNESS"),
        _text("future_orientation_suggestions", "D", "Open Feedback", "What suggestions do you have to improve future TeacherMate+ orientations?", order=14),
        _text("online_system_concerns", "D", "Open Feedback", "If you have concerns about using an online grading and class-management system, please share them.", order=15),
        _text("additional_comments", "D", "Open Feedback", "Additional comments or questions.", order=16),
    ]


def academic_heads_questions():
    experience = "A"
    experience_title = "Orientation Experience"
    technology = "C"
    technology_title = "Technology and Administrative Preferences"
    return [
        _scale("overall_rating", experience, experience_title, "Overall, how would you rate the TeacherMate+ Admin Portal orientation?", OrientationSurveyQuestion.ScaleKind.QUALITY, order=1),
        _scale("ease_understand", experience, experience_title, "How easy was it to understand the features demonstrated?", OrientationSurveyQuestion.ScaleKind.EASE, order=2, index="ORIENTATION_CLARITY"),
        _scale("demo_clarity", experience, experience_title, "How clear and easy to follow was the demonstration?", OrientationSurveyQuestion.ScaleKind.CLARITY, order=3, index="ORIENTATION_CLARITY"),
        _scale("pace", experience, experience_title, "Was the pace of the orientation appropriate?", OrientationSurveyQuestion.ScaleKind.PACE, order=4),
        _scale("confidence", experience, experience_title, "After the orientation, how confident are you in using the TeacherMate+ Admin Portal?", OrientationSurveyQuestion.ScaleKind.CONFIDENCE, order=5, index="READINESS_TO_USE"),
        _scale("readiness", experience, experience_title, "Do you feel ready to start using the TeacherMate+ Admin Portal?", OrientationSurveyQuestion.ScaleKind.READINESS, order=6, index="READINESS_TO_USE"),
        _multi("guidance_areas", "B", "Areas Needing More Guidance", "Which areas would you like additional guidance on?", _option_rows(HEADS_GUIDANCE_OPTIONS), order=7),
        _scale("personal_interaction_preference", technology, technology_title, "I prefer handling my academic administrative duties through direct and personal interaction rather than using an online system.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=8, index="TECHNOLOGY_OPENNESS"),
        _scale("technology_comfort", technology, technology_title, "I am comfortable using technology for academic administrative duties.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=9, index="TECHNOLOGY_OPENNESS"),
        _scale("open_to_teachermate", technology, technology_title, "I am open to using TeacherMate+ if it makes administrative work easier and more organized.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=10, index="TECHNOLOGY_OPENNESS"),
        _scale("monitoring_value", technology, technology_title, "TeacherMate+ can improve the monitoring of faculty and academic processes.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=11, index="PERCEIVED_ADMINISTRATIVE_VALUE"),
        _scale("standardized_procedures", technology, technology_title, "Standardized online procedures can help improve consistency across campuses.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=12, index="PERCEIVED_ADMINISTRATIVE_VALUE"),
        _scale("personal_communication_value", technology, technology_title, "Some administrative duties are still better handled through personal communication.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=13),
        _scale("paperwork_reduction", technology, technology_title, "TeacherMate+ can reduce repetitive administrative work and paperwork.", OrientationSurveyQuestion.ScaleKind.AGREEMENT, order=14, index="PERCEIVED_ADMINISTRATIVE_VALUE"),
        _text("future_training_topics", "D", "Open Feedback", "What additional topics would you like included in future TeacherMate+ training sessions?", order=15),
        _text("improvement_suggestions", "D", "Open Feedback", "Please share suggestions that can help improve TeacherMate+ or future orientations.", order=16),
        _text("additional_comments", "D", "Open Feedback", "Additional comments or questions.", order=17),
    ]


def definitions_for(survey_type):
    if survey_type == OrientationSurveySession.SurveyType.FACULTY:
        return faculty_questions()
    if survey_type == OrientationSurveySession.SurveyType.ACADEMIC_HEADS:
        return academic_heads_questions()
    raise ValueError("Unsupported orientation survey type.")
