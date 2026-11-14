"""Published workflow JSON Schema resource access."""

from importlib.resources import files


def workflow_schema_text() -> str:
    """Return the bundled Draft 2020-12 workflow document schema."""
    return (
        files("telescreen.workflow.dsl")
        .joinpath("workflow-v1.schema.json")
        .read_text(encoding="utf-8")
    )
