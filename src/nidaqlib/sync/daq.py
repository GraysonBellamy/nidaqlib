"""``Daq`` — sync entry point for :func:`~nidaqlib.tasks.open_task`.

Pattern mirrors sartoriuslib's ``Sartorius`` and alicatlib's ``Alicat``:
a thin namespace class whose classmethod opens a sync context manager
that wraps the async :func:`open_task`.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from nidaqlib.sync.portal import SyncPortal
from nidaqlib.sync.session import SyncDaqSession
from nidaqlib.tasks import open_task

if TYPE_CHECKING:
    from collections.abc import Iterator

    from nidaqlib.backend.base import DaqBackend
    from nidaqlib.tasks.spec import TaskSpec


__all__ = ["Daq"]


class Daq:
    """Sync entry-points (no instances; classmethod-only)."""

    @classmethod
    @contextlib.contextmanager  # pyright: ignore[reportDeprecated]
    def open_task(
        cls,
        spec: TaskSpec,
        *,
        backend: DaqBackend | None = None,
        timeout: float = 10.0,
    ) -> Iterator[SyncDaqSession]:
        """Open a :class:`SyncDaqSession` and tear it down on exit.

        Mirrors :func:`nidaqlib.tasks.open_task` but yields a sync session.
        Every operation on the returned session dispatches through a
        per-context :class:`SyncPortal`.

        Example::

            from nidaqlib import TaskSpec, Timing, AnalogInputVoltage
            from nidaqlib.sync import Daq

            spec = TaskSpec(
                name="ai0",
                channels=[AnalogInputVoltage(physical_channel="Dev1/ai0")],
                timing=Timing(rate_hz=1000),
            )
            with Daq.open_task(spec) as session:
                block = session.read_block(samples_per_channel=1000)
        """
        with SyncPortal() as portal:
            acm = open_task(spec, backend=backend, timeout=timeout)
            with portal.wrap_async_context_manager(acm) as async_session:
                yield SyncDaqSession(portal, async_session)
