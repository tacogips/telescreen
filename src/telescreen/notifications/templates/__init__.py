"""Versioned notification templates, built-ins, validation, rendering and resolution."""

from telescreen.notifications.templates.builtins import (
    BUILTIN_TEMPLATES,
    builtin_for,
    builtin_name,
    builtin_named,
)
from telescreen.notifications.templates.model import (
    BuiltinTemplate,
    PreviewResult,
    PreviewVariant,
    ResolvedTemplate,
    TemplateIssue,
    TemplateIssueCode,
    TemplateTexts,
    TemplateValidationError,
)
from telescreen.notifications.templates.render import render_for_channel
from telescreen.notifications.templates.repository import TemplateRepository
from telescreen.notifications.templates.resolver import (
    BuiltinTemplateResolver,
    DbTemplateResolver,
    TemplateResolver,
)
from telescreen.notifications.templates.service import (
    TemplateDetail,
    TemplateService,
    TemplateSummary,
    TemplateVersionInfo,
    TemplateVersionView,
)
from telescreen.notifications.templates.synthetic import synthetic_context
from telescreen.notifications.templates.validate import validate_template
from telescreen.notifications.templates.variables import (
    EVENT_GROUPS,
    VARIABLE_FIELDS,
    allowed_paths,
    lookup,
)

__all__: list[str] = [
    "BUILTIN_TEMPLATES",
    "EVENT_GROUPS",
    "VARIABLE_FIELDS",
    "BuiltinTemplate",
    "BuiltinTemplateResolver",
    "DbTemplateResolver",
    "PreviewResult",
    "PreviewVariant",
    "ResolvedTemplate",
    "TemplateDetail",
    "TemplateIssue",
    "TemplateIssueCode",
    "TemplateRepository",
    "TemplateResolver",
    "TemplateService",
    "TemplateSummary",
    "TemplateTexts",
    "TemplateValidationError",
    "TemplateVersionInfo",
    "TemplateVersionView",
    "allowed_paths",
    "builtin_for",
    "builtin_name",
    "builtin_named",
    "lookup",
    "render_for_channel",
    "synthetic_context",
    "validate_template",
]
