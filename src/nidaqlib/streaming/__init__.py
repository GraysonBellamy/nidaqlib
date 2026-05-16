"""Streaming acquisition surface.

Two recorders, one per acquisition model:

- :func:`record` — hardware-clocked block path, emits :class:`DaqBlock`.
- :func:`record_polled` — software-timed scalar path, emits :class:`DaqReading`.

Don't unify them; they have different correctness models.

Both yield a :class:`Recording[T]` from their context manager. The
``Recording`` wraps the payload stream, the live :class:`AcquisitionSummary`,
and the active ``rate_hz`` so consumers can read all three from one object.
"""

from __future__ import annotations

from nidaqlib.streaming._types import Recording
from nidaqlib.streaming.block import (
    AcquisitionSummary,
    ErrorPolicy,
    OverflowPolicy,
    record,
)
from nidaqlib.streaming.poll_source import PollSource, PollSourceAdapter
from nidaqlib.streaming.recorder import record_polled

__all__ = [
    "AcquisitionSummary",
    "ErrorPolicy",
    "OverflowPolicy",
    "PollSource",
    "PollSourceAdapter",
    "Recording",
    "record",
    "record_polled",
]
