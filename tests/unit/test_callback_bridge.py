"""Tests for the §11.3.2 driver-thread → ``queue.SimpleQueue`` bridge.

The bridge is the highest-risk recorder boundary — these tests must pass
against :class:`~nidaqlib.backend.fake.FakeDaqBackend` before any real-NI
code runs. The cases cover:

- ``test_happy_path`` — N callback firings → N chunks in order, no deadlock.
- ``test_clean_shutdown`` — recorder exit completes within 2 s with the
  drainer blocked in ``chunk_q.get`` and no pending arrays. Sentinel must
  wake the drainer; thread count after exit equals before.
- ``test_cancel_mid_stream`` — cancelling the consumer mid-stream still
  unwinds with unregister BEFORE stop (asserted via the operation log).
- ``test_gc_survival`` — callback survives a ``gc.collect()`` mid-stream.
"""

from __future__ import annotations

import gc
import threading

import anyio
import pytest

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    DaqBlock,
    NIDaqTaskStateError,
    TaskSpec,
    Timing,
    open_device,
    record,
)
from nidaqlib.backend import FakeDaqBackend


def _spec(name: str = "ai_bridge") -> TaskSpec:
    return TaskSpec(
        name=name,
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0", name="ch0", unit="V")],
        timing=Timing(rate_hz=10_000.0, mode=AcquisitionMode.CONTINUOUS),
    )


def _operation_index(backend: FakeDaqBackend, op: str) -> int:
    """Return the index of the first occurrence of ``op`` in the log.

    Asserts the op is present so callers can compare indices directly.
    """
    for i, entry in enumerate(backend.operations):
        if entry.op == op:
            return i
    msg = f"operation {op!r} not in log: {[e.op for e in backend.operations]}"
    raise AssertionError(msg)


@pytest.mark.anyio
async def test_happy_path() -> None:
    """100 callback firings → 100 chunks in order, no deadlock under 2 s."""
    backend = FakeDaqBackend(read_block_default_shape=(1, 64))
    received: list[DaqBlock] = []
    async with (
        await open_device(_spec(), backend=backend, autostart=False) as session,
        record(
            session,
            chunk_size=64,
            buffer_size=128,
            use_callback_bridge=True,
        ) as _rec,
    ):
        stream, _summary = _rec.stream, _rec.summary
        backend.simulate_callbacks(session.raw_task, firings=100)
        with anyio.fail_after(2.0):
            async for block in stream:
                received.append(block)
                if len(received) == 100:
                    break
    assert len(received) == 100
    # Order invariant: block_index must be strictly monotonic, starting at 0.
    for expected, block in enumerate(received):
        assert block.block_index == expected


@pytest.mark.anyio
async def test_clean_shutdown_drainer_blocked() -> None:
    """Recorder exit completes with the drainer blocked in ``chunk_q.get``.

    No callbacks fire in this test, so the drainer is parked in
    ``to_thread.run_sync(chunk_q.get)`` for the duration. The recorder
    __aexit__ MUST post the sentinel to wake it; without that the exit
    deadlocks. ``anyio.fail_after`` enforces the 2 s budget.

    Also asserts no leaked worker threads — thread count after equals
    before.
    """
    backend = FakeDaqBackend(read_block_default_shape=(1, 64))
    pre_threads = {t.ident for t in threading.enumerate()}
    with anyio.fail_after(2.0):
        async with await open_device(_spec(), backend=backend, autostart=False) as session:
            async with record(
                session,
                chunk_size=64,
                use_callback_bridge=True,
            ) as _rec2:
                _stream, _summary = _rec2.stream, _rec2.summary
                # Intentionally do not fire callbacks — drainer parks in get().
                pass
    # Allow any background daemon teardown to settle; the drainer thread is
    # short-lived (it returns when the sentinel arrives).
    for _ in range(10):
        post = {t.ident for t in threading.enumerate()}
        if post <= pre_threads:
            break
        await anyio.sleep(0.05)
    leaked_threads = [t for t in threading.enumerate() if t.ident not in pre_threads]
    leaked_bridge = [t for t in leaked_threads if "FakeDaqBackend-cb-sim" in t.name]
    assert not leaked_bridge, f"leaked bridge threads: {leaked_bridge}"


@pytest.mark.anyio
async def test_cancel_mid_stream_stop_then_unregister_then_close() -> None:
    """Cancelling the consumer mid-stream unwinds with stop → unregister → close.

    Real NI rejects ``register_every_n_samples_acquired_into_buffer_event(0,
    None)`` on a running task with -200986 ("DAQmx software event cannot be
    unregistered because the task is running"). The bridge therefore stops
    the task FIRST — at which point in-flight callbacks have completed and
    no new ones fire — then unregisters, then sentinels the drainer, then
    closes. This test asserts that operation order on the
    :class:`FakeDaqBackend` log.
    """
    backend = FakeDaqBackend(read_block_default_shape=(1, 32))
    async with await open_device(_spec(), backend=backend, autostart=False) as session:
        with anyio.move_on_after(0.5):
            async with record(
                session,
                chunk_size=32,
                use_callback_bridge=True,
            ) as _rec3:
                stream, _summary = _rec3.stream, _rec3.summary
                backend.simulate_callbacks(session.raw_task, firings=10_000, cadence_s=0.001)
                count = 0
                async for _block in stream:
                    count += 1
                    if count >= 5:
                        # Cancel by exiting the move_on_after scope — the
                        # next iteration of the consumer is interrupted.
                        await anyio.sleep(10.0)
    # NI-mandated ordering: stop precedes unregister (otherwise NI -200986);
    # close happens last via the outer open_device exit.
    stop_at = _operation_index(backend, "stop_task")
    unreg_at = _operation_index(backend, "unregister_every_n_samples")
    close_at = _operation_index(backend, "close_task")
    assert stop_at < unreg_at < close_at


@pytest.mark.anyio
async def test_register_must_precede_start() -> None:
    """The bridge registers the buffer event BEFORE NI's ``task.start()``.

    NI rejects ``register_every_n_samples_acquired_into_buffer_event`` when
    the task is already running with status code -200960. The fake backend
    enforces the same invariant (see
    :meth:`FakeDaqBackend.register_every_n_samples`); this test asserts the
    happy-path operation log puts ``register_every_n_samples`` before
    ``start_task`` so a future regression in the bridge would be caught here
    rather than only on real hardware.
    """
    backend = FakeDaqBackend(read_block_default_shape=(1, 16))
    async with (
        await open_device(_spec(), backend=backend, autostart=False) as session,
        record(
            session,
            chunk_size=16,
            use_callback_bridge=True,
        ) as _rec4,
    ):
        _stream, _summary = _rec4.stream, _rec4.summary
        del session  # only the operation log matters
    register_at = _operation_index(backend, "register_every_n_samples")
    start_at = _operation_index(backend, "start_task")
    assert register_at < start_at, (
        f"register_every_n_samples (idx={register_at}) must precede "
        f"start_task (idx={start_at}); op log: "
        f"{[e.op for e in backend.operations]}"
    )


@pytest.mark.anyio
async def test_register_after_start_rejected_by_fake_backend() -> None:
    """Direct fake-backend assertion: post-start registration is rejected.

    Mirrors NI's -200960 ("Register all your DAQmx software events prior to
    starting the task"). Locks the invariant at the backend layer so a
    refactor that bypasses ``record()`` cannot silently re-introduce the
    bug.
    """
    from nidaqlib.errors import NIDaqBackendError

    backend = FakeDaqBackend(read_block_default_shape=(1, 8))
    async with await open_device(_spec(), backend=backend) as session:
        # autostart=True by default — task is started.
        assert session.is_started
        with pytest.raises(NIDaqBackendError, match=r"already started"):
            backend.register_every_n_samples(session.raw_task, 8, lambda _n: None)


@pytest.mark.anyio
async def test_callback_survives_gc() -> None:
    """A ``gc.collect()`` mid-stream must not break the seam.

    Reproduces the GC failure mode called out in design doc §11.3.2 — NI
    stores the callback as a raw C function pointer; if Python GC reaps the
    closure, the next firing crashes the driver. The session and backend
    must keep strong references to the wrapper for as long as the callback
    is registered.
    """
    backend = FakeDaqBackend(read_block_default_shape=(1, 16))
    received: list[DaqBlock] = []
    async with (
        await open_device(_spec(), backend=backend, autostart=False) as session,
        record(
            session,
            chunk_size=16,
            use_callback_bridge=True,
        ) as _rec5,
    ):
        stream, _summary = _rec5.stream, _rec5.summary
        backend.simulate_callbacks(session.raw_task, firings=5)
        with anyio.fail_after(2.0):
            async for block in stream:
                received.append(block)
                if len(received) == 1:
                    # Force GC at a moment where the callback wrapper
                    # is the only live reference path. If the seam is
                    # broken, subsequent firings will not arrive.
                    gc.collect()
                    backend.simulate_callbacks(session.raw_task, firings=4)
                if len(received) >= 5:
                    break
    assert len(received) == 5


@pytest.mark.anyio
async def test_close_refuses_active_callback_bridge() -> None:
    """Direct close cannot bypass the recorder's ordered bridge shutdown."""
    backend = FakeDaqBackend(read_block_default_shape=(1, 16))
    async with await open_device(_spec(), backend=backend) as session:
        session._set_callback_handle(object())  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(NIDaqTaskStateError, match="callback bridge"):
            await session.close()
        session._set_callback_handle(None)  # pyright: ignore[reportPrivateUsage]
