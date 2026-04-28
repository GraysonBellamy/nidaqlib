"""Tasks — specs, acquisition records, sessions, and :func:`open_task`."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from nidaqlib.tasks.builder import TaskBuilder
from nidaqlib.tasks.models import DaqBlock, DaqReading
from nidaqlib.tasks.session import DaqSession
from nidaqlib.tasks.spec import AcquisitionMode, Edge, TaskSpec, Timing

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from nidaqlib.backend.base import DaqBackend


@asynccontextmanager
async def open_task(
    spec: TaskSpec,
    *,
    backend: DaqBackend | None = None,
    timeout: float = 10.0,  # noqa: ASYNC109 — NI per-call timeout, not coroutine
) -> AsyncGenerator[DaqSession]:
    """Open a :class:`DaqSession` for ``spec`` and start it.

    The session is started on entry and closed on exit (whether the body
    succeeds or raises). Mirrors the ecosystem ``open_device`` shape used by
    ``alicatlib`` and ``sartoriuslib``, but the object being opened is a DAQ
    task, not a serial device.

    Args:
        spec: Declarative :class:`TaskSpec` to materialise.
        backend: Optional :class:`~nidaqlib.backend.base.DaqBackend`. Defaults
            to :class:`~nidaqlib.backend.nidaqmx_backend.NidaqmxBackend` —
            tests typically pass a
            :class:`~nidaqlib.backend.fake.FakeDaqBackend` here.
        timeout: Default per-operation timeout, in seconds.

    Yields:
        A started :class:`DaqSession`.
    """
    if backend is None:
        # Local import — keeps the production `nidaqmx` import off the
        # critical path of test sessions that supply a fake backend.
        from nidaqlib.backend.nidaqmx_backend import NidaqmxBackend  # noqa: PLC0415

        backend = NidaqmxBackend()
    session = DaqSession(spec, backend, timeout=timeout)
    try:
        await session.start()
        yield session
    finally:
        await session.close()


__all__ = [
    "AcquisitionMode",
    "DaqBlock",
    "DaqReading",
    "DaqSession",
    "Edge",
    "TaskBuilder",
    "TaskSpec",
    "Timing",
    "open_task",
]
