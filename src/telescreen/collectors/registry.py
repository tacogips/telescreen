"""Registry for source-specific collector adapters and sweep capabilities."""

from dataclasses import dataclass, field

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import CollectorKind, ErrorCode
from telescreen.ports.collectors import CollectorAdapter
from telescreen.ports.sweeps import SweepCapable


@dataclass
class CollectorRegistry:
    """Resolve collector implementations by their public source kind."""

    adapters: list[tuple[CollectorKind, CollectorAdapter]] = field(default_factory=list)
    sweepers: list[tuple[CollectorKind, SweepCapable]] = field(default_factory=list)

    def register(self, adapter: CollectorAdapter, sweeper: SweepCapable | None = None) -> None:
        """Register an adapter and its optional whole-estate capability."""
        self.adapters = [item for item in self.adapters if item[0] is not adapter.kind]
        self.adapters.append((adapter.kind, adapter))
        if sweeper is not None:
            self.sweepers = [item for item in self.sweepers if item[0] is not adapter.kind]
            self.sweepers.append((adapter.kind, sweeper))

    def adapter(self, kind: CollectorKind) -> CollectorAdapter:
        """Return an adapter or raise a safe not-found error."""
        for registered_kind, adapter in self.adapters:
            if registered_kind is kind:
                return adapter
        raise TelescreenError(ErrorCode.NOT_FOUND, "collector adapter is not registered")

    def sweeper(self, kind: CollectorKind) -> SweepCapable | None:
        """Return the optional sweep capability for a kind."""
        return next(
            (sweeper for registered_kind, sweeper in self.sweepers if registered_kind is kind),
            None,
        )
