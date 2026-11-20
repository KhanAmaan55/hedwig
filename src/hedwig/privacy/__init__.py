"""The user's control over their data: export everything, erase for real (docs/21 §7, ADR-0024).

The one module that reads and deletes across every table. That is the documented exception to
"writes go through the owner" (docs/08 §3.1): purge must be exhaustive, and only something that
sees the whole file can prove it was.
"""

from hedwig.privacy.export import Exporter
from hedwig.privacy.purge import Purger

__all__ = ["Exporter", "Purger"]
