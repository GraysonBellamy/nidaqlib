"""Streaming acquisition surface.

Two recorders, one per acquisition model (design doc §11.3 / §13.1):

- :func:`record` — hardware-clocked block path, emits :class:`DaqBlock`.
- :func:`record_polled` — software-timed scalar path, emits :class:`DaqReading`.

Don't unify them; they have different correctness models.
"""

from __future__ import annotations

from nidaqlib.streaming.block import (
    AcquisitionSummary,
    ErrorPolicy,
    OverflowPolicy,
    record,
)
from nidaqlib.streaming.recorder import record_polled

__all__ = [
    "AcquisitionSummary",
    "ErrorPolicy",
    "OverflowPolicy",
    "record",
    "record_polled",
]
