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
    autostart: bool = True,
    confirm_start: bool = False,
) -> AsyncGenerator[DaqSession]:
    """Open a :class:`DaqSession` for ``spec`` and (optionally) start it.

    The session is closed on exit (whether the body succeeds or raises).
    Mirrors the ecosystem ``open_device`` shape used by ``alicatlib`` and
    ``sartoriuslib``, but the object being opened is a DAQ task, not a
    serial device.

    Args:
        spec: Declarative :class:`TaskSpec` to materialise.
        backend: Optional :class:`~nidaqlib.backend.base.DaqBackend`. Defaults
            to :class:`~nidaqlib.backend.nidaqmx_backend.NidaqmxBackend` —
            tests typically pass a
            :class:`~nidaqlib.backend.fake.FakeDaqBackend` here.
        timeout: Default per-operation timeout, in seconds.
        autostart: When ``True`` (default), the session is configured AND
            started before the body runs. When ``False``, the session is
            only configured — the caller is responsible for ``await
            session.start()`` before any acquisition. Required for the
            §11.3.2 callback bridge, which must register the buffer event
            before NI's ``task.start()``; pass the unstarted session to
            :func:`~nidaqlib.streaming.block.record` with
            ``use_callback_bridge=True`` and the recorder owns the start.
        confirm_start: Required when starting the task can actuate hardware
            immediately (for example counter-output pulse trains). Only
            consulted when ``autostart=True``.

    Yields:
        A configured :class:`DaqSession`. Started iff ``autostart=True``.
    """
    if backend is None:
        # Local import — keeps the production `nidaqmx` import off the
        # critical path of test sessions that supply a fake backend.
        from nidaqlib.backend.nidaqmx_backend import NidaqmxBackend  # noqa: PLC0415

        backend = NidaqmxBackend()
    session = DaqSession(spec, backend, timeout=timeout)
    if autostart:
        # Validate up-front so a missing ``confirm_start`` for an actuating
        # task fails before any backend resources are allocated.
        session._validate_start_safety(confirm=confirm_start)  # pyright: ignore[reportPrivateUsage]
    try:
        await session.configure()
        if autostart:
            await session.start(confirm=confirm_start)
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
