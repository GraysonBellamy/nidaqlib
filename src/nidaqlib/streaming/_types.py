"""Streaming types shared across the recorder modules.

Lives in its own module to avoid the ``streaming/__init__.py`` ↔
``streaming/block.py`` circular import that would arise if :class:`Recording`
were defined at the package root.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from nidaqlib.streaming.block import AcquisitionSummary


@dataclass(slots=True)
class Recording[T]:
    """Active-recording handle returned by :func:`record` / :func:`record_polled`.

    Attributes:
        stream: Async iterator of payloads. Closes when the recorder
            context manager exits.
        summary: Mutable :class:`AcquisitionSummary` updated in place during
            the run. ``summary.finished_at`` is set on context exit.
        rate_hz: Configured cadence of the active recording. ``None`` for
            on-demand mode.
    """

    stream: AsyncIterator[T]
    summary: AcquisitionSummary
    rate_hz: float | None


__all__ = ["Recording"]
