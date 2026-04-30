"""Sync wrappers for :func:`record` and :func:`record_polled`.

Each wrapper owns its own :class:`SyncPortal` and yields a sync iterator
of :class:`DaqBlock` (or :class:`DaqReading`) records.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, cast

from nidaqlib.streaming import (
    AcquisitionSummary,
    ErrorPolicy,
    OverflowPolicy,
)
from nidaqlib.streaming import (
    record as _async_record,
)
from nidaqlib.streaming import (
    record_polled as _async_record_polled,
)
from nidaqlib.sync.portal import SyncAsyncIterator, SyncPortal

if TYPE_CHECKING:
    from collections.abc import Iterator

    from nidaqlib.sync.session import SyncDaqSession
    from nidaqlib.tasks.models import DaqBlock, DaqReading


__all__ = ["AcquisitionSummary", "ErrorPolicy", "OverflowPolicy", "record", "record_polled"]


@contextlib.contextmanager  # pyright: ignore[reportDeprecated]
def record(
    source: SyncDaqSession,
    *,
    chunk_size: int,
    timeout: float = 10.0,
    buffer_size: int = 16,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
    use_callback_bridge: bool = False,
) -> Iterator[tuple[SyncAsyncIterator[DaqBlock], AcquisitionSummary]]:
    """Sync wrapper around :func:`nidaqlib.streaming.record`.

    Yields ``(stream, summary)``. The stream is a sync iterator producing
    :class:`DaqBlock` records; iterate it with a normal ``for`` loop.

    Example::

        with (
            Daq.open_device(spec) as session,
            record(session, chunk_size=1000) as (stream, summary),
        ):
            for block in stream:
                process(block)
    """
    with SyncPortal() as portal:
        acm = _async_record(
            source._session,  # pyright: ignore[reportPrivateUsage]
            chunk_size=chunk_size,
            timeout=timeout,
            buffer_size=buffer_size,
            error_policy=error_policy,
            overflow=overflow,
            use_callback_bridge=use_callback_bridge,
        )
        with portal.wrap_async_context_manager(acm) as (rx, summary):
            sync_iter = portal.wrap_async_iter(rx)
            try:
                yield sync_iter, summary
            finally:
                sync_iter.close()


@contextlib.contextmanager  # pyright: ignore[reportDeprecated]
def record_polled(
    source: SyncDaqSession,
    *,
    rate_hz: float,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.BLOCK,
    buffer_size: int = 64,
) -> Iterator[tuple[SyncAsyncIterator[DaqReading], AcquisitionSummary]]:
    """Sync wrapper around :func:`nidaqlib.streaming.record_polled`.

    The sync facade only accepts a session source — the manager-mode
    fan-out belongs to async-only call sites — so the per-tick payload is
    always :class:`DaqReading`.
    """
    with SyncPortal() as portal:
        acm = _async_record_polled(
            source._session,  # pyright: ignore[reportPrivateUsage]
            rate_hz=rate_hz,
            error_policy=error_policy,
            overflow=overflow,
            buffer_size=buffer_size,
        )
        with portal.wrap_async_context_manager(acm) as (rx, summary):
            # The session-source overload always emits DaqReading; the
            # async-side Union is widened only for manager-mode.
            reading_rx = cast("SyncAsyncIterator[DaqReading]", portal.wrap_async_iter(rx))
            try:
                yield reading_rx, summary
            finally:
                reading_rx.close()
