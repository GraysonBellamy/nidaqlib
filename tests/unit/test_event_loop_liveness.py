"""Asserts that ``read_block`` does not block the event loop.

Adjacent to the callback-bridge tests, this is the canary that catches a
synchronous NI call slipping into the ``await`` chain (i.e., losing the
``anyio.to_thread.run_sync`` boundary).

A 10 ms heartbeat counter runs concurrently with a slow ``read_block`` and
must continue ticking with bounded drift.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import anyio
import pytest

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    TaskSpec,
    Timing,
    open_device,
)
from nidaqlib.backend.fake import FakeDaqBackend, _FakeTask  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    import numpy as np


class _SlowReadBackend(FakeDaqBackend):
    """Fake backend whose ``read_block`` blocks for ``delay_s`` per call.

    Models a slow NI driver read. If the wrapper loses its
    ``to_thread.run_sync`` boundary, the heartbeat coroutine will not get
    scheduled during these calls and the test will fail under
    ``anyio.fail_after``.
    """

    def __init__(self, *, delay_s: float, shape: tuple[int, int]) -> None:
        super().__init__(read_block_default_shape=shape)
        self._delay_s = delay_s

    def read_block(
        self,
        task: _FakeTask,
        samples_per_channel: int,
        timeout: float,
    ) -> np.ndarray:
        # Synchronous sleep — this MUST run on a worker thread for the
        # event loop to stay live.
        time.sleep(self._delay_s)
        return super().read_block(task, samples_per_channel, timeout)


@pytest.mark.anyio
async def test_read_block_does_not_block_event_loop() -> None:
    spec = TaskSpec(
        name="ai_liveness",
        channels=[AnalogInputVoltage(physical_channel="Dev1/ai0", name="ch0", unit="V")],
        timing=Timing(rate_hz=1000.0, mode=AcquisitionMode.CONTINUOUS),
    )
    ticks: list[float] = []
    stop = anyio.Event()
    main_thread_id = threading.get_ident()
    # Confirm the read truly happened on a worker (sanity for the test
    # itself, not the production code).
    read_thread_ids: list[int] = []

    class _SpyBackend(_SlowReadBackend):
        def read_block(
            self,
            task: _FakeTask,
            samples_per_channel: int,
            timeout: float,
        ) -> np.ndarray:
            read_thread_ids.append(threading.get_ident())
            return super().read_block(task, samples_per_channel, timeout)

    spy = _SpyBackend(delay_s=0.5, shape=(1, 1000))

    async def _heartbeat() -> None:
        while not stop.is_set():
            ticks.append(time.monotonic())
            with anyio.move_on_after(0.01):
                await stop.wait()

    async with await open_device(spec, backend=spy) as session, anyio.create_task_group() as tg:
        _ = tg.start_soon(_heartbeat)
        with anyio.fail_after(2.0):
            # Issue one slow read that takes 500 ms.
            await session.read_block(1000)
        stop.set()

    # The read must have run on a thread other than the event loop's.
    assert read_thread_ids
    assert all(tid != main_thread_id for tid in read_thread_ids)

    # We expect ~50 ticks at 10 ms intervals over a 500 ms read. Allow wide
    # margins for CI scheduler jitter — the canary value is "many ticks
    # land during the read", not "exactly N at exactly 10 ms".
    assert len(ticks) >= 20, f"only {len(ticks)} heartbeats during a 500 ms read"
    # Drift bound: total elapsed should be roughly len(ticks) * 0.01 within
    # ~50% slack. A blocked event loop produces << expected ticks, which
    # the assertion above already catches; this is the secondary guard.
    if len(ticks) >= 2:
        elapsed = ticks[-1] - ticks[0]
        # Average inter-tick should be well under 30 ms (3x nominal).
        avg_interval = elapsed / max(len(ticks) - 1, 1)
        assert avg_interval < 0.030, f"heartbeat drifted to {avg_interval:.3f}s/tick"
