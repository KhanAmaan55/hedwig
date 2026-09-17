"""Layer 1 — Edge.

Translates the outside world into commands and streams results back. Contains no domain
logic: it validates, authenticates, translates and streams (docs/02 §3).
"""

from __future__ import annotations

from hedwig.api.app import create_app

__all__ = ["create_app"]
