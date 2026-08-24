"""Provider interface.

Deliberately small. A provider answers two questions: can you run right now, and
what is the most recent radar scene over this box. Everything else the models
need they derive themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Tuple


@dataclass
class ProviderStatus:
    """Whether a provider can run, and if not, precisely what is missing."""
    name: str
    available: bool
    reason: str
    requires: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"provider": self.name, "available": self.available,
                "reason": self.reason, "requires": self.requires}


@dataclass
class SceneRef:
    """A radar granule that exists, without downloading it."""
    id: str
    acquired: str          # ISO 8601
    platform: str
    mode: str
    polarisation: str
    footprint_bbox: Tuple[float, float, float, float]
    download_url: Optional[str] = None
    age_hours: Optional[float] = None

    def to_dict(self) -> dict:
        return {"id": self.id, "acquired": self.acquired,
                "platform": self.platform, "mode": self.mode,
                "polarisation": self.polarisation,
                "bbox": list(self.footprint_bbox),
                "age_hours": self.age_hours}


class Provider(Protocol):
    """What every data source must be able to do."""

    name: str

    def status(self) -> ProviderStatus:
        """Can this provider run here and now?"""

    def search_scenes(self, bbox: Tuple[float, float, float, float],
                      days: int = 12, limit: int = 10) -> List[SceneRef]:
        """Radar granules intersecting ``bbox`` in the last ``days``."""
