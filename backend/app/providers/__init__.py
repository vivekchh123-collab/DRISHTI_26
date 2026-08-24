"""Data providers: the seam between the models and where the numbers come from.

Everything above this package works on arrays and knows nothing about where they
came from. That is what makes the demo-to-live switch a configuration change
rather than a rewrite.

``resolve()`` picks a provider from the environment. With no credentials
configured it returns the demo provider and says so; the models are identical
either way, and the provenance block on every API response reports which was
used.
"""

from __future__ import annotations

import os
from typing import List

from .base import Provider, ProviderStatus
from .copernicus import CopernicusProvider
from .cwc import CWCProvider
from .demo import DemoProvider
from .openmeteo import OpenMeteoProvider

__all__ = ["Provider", "ProviderStatus", "DemoProvider", "CopernicusProvider",
           "CWCProvider", "OpenMeteoProvider", "resolve", "status_report"]


def resolve() -> Provider:
    """The best provider the current environment can support."""
    cop = CopernicusProvider()
    if cop.status().available:
        return cop
    return DemoProvider()


def status_report() -> List[dict]:
    """What every provider would do right now, and why.

    Surfaced at ``/api/methods`` so the answer to "is this live?" is a fact the
    system reports rather than a claim someone makes.
    """
    return [p.status().to_dict() for p in (
        OpenMeteoProvider(), CopernicusProvider(), CWCProvider(), DemoProvider())]
