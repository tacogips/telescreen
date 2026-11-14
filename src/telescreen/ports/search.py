"""Post-commit search indexing port."""

from typing import Protocol


class SearchIndexer(Protocol):
    """Index committed records; implementations handle their own failures."""

    async def index_issue(self, issue_id: str) -> None:
        """Index an issue after its transaction commits."""
        ...

    async def index_finding(self, finding_id: str) -> None:
        """Index a finding after its transaction commits."""
        ...

    async def index_run(self, run_id: str) -> None:
        """Index a workflow run after its transaction commits."""
        ...
