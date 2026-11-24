"""Read models for "what happened, and is HEDWIG healthy?" (docs/19 §13, OBS-01).

Like `explain`, this package owns no table: it assembles records other modules write.
"""

from hedwig.telemetry.metrics import render_metrics
from hedwig.telemetry.traces import Traces

__all__ = ["Traces", "render_metrics"]
