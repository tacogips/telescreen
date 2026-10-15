"""Static guard that watched projection models use ORM unit-of-work writes."""

import ast
from pathlib import Path

_WATCHED: frozenset[str] = frozenset(
    {
        "MemberRow",
        "TeamRow",
        "TeamMembershipRow",
        "MemberIdentityRow",
        "ApiTokenRow",
        "OwnershipRuleRow",
        "CollectorConfigRow",
        "IssueRow",
    }
)
_DML: frozenset[str] = frozenset({"update", "delete", "insert"})


def test_watched_models_have_no_core_dml_calls() -> None:
    """Reject direct SQLAlchemy Core DML on projected models outside DB migrations."""
    root: Path = Path(__file__).parents[2] / "src" / "telescreen"
    failures: list[str] = []
    for path in root.rglob("*.py"):
        if "db" in path.relative_to(root).parts:
            continue
        tree: ast.Module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id in _DML and node.args:
                target: ast.expr = node.args[0]
                if isinstance(target, ast.Name) and target.id in _WATCHED:
                    failures.append(f"{path}:{node.lineno}: {node.func.id}({target.id})")
    assert failures == []
