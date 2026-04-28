"""Sync wrapper around :class:`~nidaqlib.tasks.session.DaqSession`.

Mirrors the async session API one-for-one, dispatching every call through
the bound :class:`SyncPortal`. The class itself is bound to one portal —
reusing a :class:`SyncDaqSession` after its portal has exited is an
error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from nidaqlib.sync.portal import SyncPortal
    from nidaqlib.tasks.models import DaqBlock, DaqReading
    from nidaqlib.tasks.session import DaqSession
    from nidaqlib.tasks.spec import TaskSpec


__all__ = ["SyncDaqSession"]


class SyncDaqSession:
    """Sync facade over an open :class:`DaqSession`."""

    def __init__(self, portal: SyncPortal, session: DaqSession) -> None:
        self._portal = portal
        self._session = session

    @property
    def spec(self) -> TaskSpec:
        """The :class:`TaskSpec` this session was constructed from."""
        return self._session.spec

    @property
    def is_started(self) -> bool:
        """``True`` between :meth:`start` and :meth:`stop`."""
        return self._session.is_started

    @property
    def is_closed(self) -> bool:
        """``True`` once :meth:`close` has run."""
        return self._session.is_closed

    @property
    def raw_task(self) -> Any:
        """The underlying backend task handle (escape hatch — design §7.4)."""
        return self._session.raw_task

    def stop(self) -> None:
        """Stop the underlying task. Idempotent."""
        self._portal.call(self._session.stop)

    def close(self) -> None:
        """Stop and close the underlying task. Idempotent."""
        self._portal.call(self._session.close)

    def read_block(
        self,
        samples_per_channel: int,
        *,
        timeout: float | None = None,
    ) -> DaqBlock:
        """Read one rectangular :class:`DaqBlock`."""
        return self._portal.call(self._session.read_block, samples_per_channel, timeout=timeout)

    def acquire(
        self,
        samples_per_channel: int,
        *,
        timeout: float | None = None,
    ) -> DaqBlock:
        """Run one finite acquisition and return its :class:`DaqBlock`."""
        return self._portal.call(self._session.acquire, samples_per_channel, timeout=timeout)

    def poll(self, *, timeout: float | None = None) -> DaqReading:
        """One-shot scalar read across all channels."""
        return self._portal.call(self._session.poll, timeout=timeout)

    def write(
        self,
        values: Mapping[str, float | bool],
        *,
        confirm: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Write one sample-per-channel to the task's output channels.

        Sync wrapper around :meth:`DaqSession.write`. The safety gate
        (``confirm`` + ``safe_min`` / ``safe_max``) runs in the same
        process, before any I/O.
        """
        self._portal.call(
            self._session.write,
            values,
            confirm=confirm,
            timeout=timeout,
        )
