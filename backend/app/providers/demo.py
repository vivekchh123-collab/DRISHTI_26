"""The offline provider.

Reports honestly that it has no observations. It exists so that ``resolve()``
always returns something and the application never has a code path that depends
on credentials being present.
"""

from __future__ import annotations

from typing import List, Tuple

from .base import Provider, ProviderStatus, SceneRef


class DemoProvider(Provider):
    name = "demo"

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            available=True,
            reason=("Offline mode. Input rasters are modelled, not observed; "
                    "the algorithms are unchanged and are the published methods "
                    "listed at /api/methods."),
            requires=[],
        )

    def search_scenes(self, bbox: Tuple[float, float, float, float],
                      days: int = 12, limit: int = 10) -> List[SceneRef]:
        # Deliberately empty. Returning invented granule identifiers would make
        # the interface look live when it is not, which is the one thing this
        # provider exists to avoid.
        return []
