from django import template

from ..scenario_content import render_scenario_content
from ..question_content import render_question_choice_content, render_question_content
from ..questionnaire_presentation import render_questionnaire_content


register = template.Library()


@register.filter
def scenario_content(value, content_format):
    return render_scenario_content(value, content_format)


@register.filter
def question_content(value, content_format):
    return render_question_content(value, content_format)


@register.filter
def question_choice_content(value, content_format):
    return render_question_choice_content(value, content_format)


@register.filter
def questionnaire_question_content(value, content_format):
    return render_questionnaire_content(value, content_format)


@register.filter
def questionnaire_choice_content(value, content_format):
    return render_questionnaire_content(value, content_format, field="choice_a")
