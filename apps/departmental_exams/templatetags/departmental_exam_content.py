from django import template

from ..scenario_content import render_scenario_content


register = template.Library()


@register.filter
def scenario_content(value, content_format):
    return render_scenario_content(value, content_format)
