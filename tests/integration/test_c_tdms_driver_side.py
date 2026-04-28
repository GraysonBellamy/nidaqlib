"""Block C — driver-side TDMS logging on real TC hardware (design doc §14.6).

NI's TDMS path is configured on the task itself via
``task.in_stream.configure_logging``. ``nidaqlib`` exposes that via
:class:`TdmsLogging`. Two modes matter here:

- ``LoggingMode.LOG`` — write-only. The application read path is bypassed,
  so :func:`record` must short-circuit to an empty stream rather than block
  forever in ``read_block``.
- ``LoggingMode.LOG_AND_READ`` — both the TDMS file *and* the recorder
  receive samples.

Both branches are validated end-to-end here; the TDMS file is opened with
``npTDMS`` to confirm the driver actually wrote samples.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import anyio
import pytest

from nidaqlib import (
    DaqBlock,
    TaskSpec,
    TdmsLogging,
    open_task,
    record,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from .conftest import TcHardwareConfig


pytestmark = pytest.mark.anyio


class _TdmsChannel(Protocol):
    def __len__(self) -> int: ...


class _TdmsGroup(Protocol):
    def channels(self) -> Sequence[_TdmsChannel]: ...


class _TdmsReader(Protocol):
    def groups(self) -> Sequence[_TdmsGroup]: ...


class _TdmsFileFactory(Protocol):
    def open(self, path: Path) -> AbstractContextManager[_TdmsReader]: ...


def _tdms_file_factory() -> _TdmsFileFactory:
    module = pytest.importorskip("nptdms", reason="nptdms not installed")
    return cast("_TdmsFileFactory", vars(module)["TdmsFile"])


# ---------------------------------------------------------------------------
# C1 — LoggingMode.LOG: empty stream, file populated
# ---------------------------------------------------------------------------


async def test_c1_tdms_log_only_emits_empty_stream(
    tc_config: TcHardwareConfig,
    tc_spec_continuous: TaskSpec,
    hw_tmp_dir: Path,
) -> None:
    """``LoggingMode.LOG`` runs without deadlocking and writes a TDMS file.

    The recorder MUST detect log-only logging and emit an empty stream
    (``summary.blocks_emitted == 0``); it MUST NOT block forever in
    ``read_block`` waiting for samples that the driver consumed before
    the application could see them.
    """
    from nidaqmx.constants import LoggingMode

    tdms_file = _tdms_file_factory()
    tdms_path = hw_tmp_dir / "c1.tdms"
    spec = tc_spec_continuous.replace(logging=TdmsLogging(path=tdms_path, mode=LoggingMode.LOG))
    duration_s = 2.0

    async with (
        open_task(spec) as session,
        record(session, chunk_size=max(2, int(tc_config.rate_hz // 2))) as (
            rx,
            summary,
        ),
    ):
        # The recorder must short-circuit — `record_started_at` should
        # complete almost immediately rather than blocking on a buffered
        # read. We cap the wait at 1 s so a regression here surfaces as a
        # test failure, not a hung suite.
        with anyio.fail_after(1.0):
            consumed = 0
            async for _block in rx:  # pragma: no cover - empty stream
                consumed += 1
            assert consumed == 0
        # Hold the task open long enough for NI to write samples into TDMS.
        await anyio.sleep(duration_s)

    assert summary.blocks_emitted == 0

    # File should exist and contain at least one channel of samples.
    assert tdms_path.exists(), f"TDMS file not written: {tdms_path}"
    with tdms_file.open(tdms_path) as tdms:
        groups = tdms.groups()
        assert groups, "TDMS file contains no groups"
        # Sum samples across all channels in all groups.
        total_samples = 0
        for group in groups:
            for channel in group.channels():
                total_samples += len(channel)
        # At ~rate_hz Hz for ``duration_s`` seconds, expect roughly
        # rate_hz * duration_s samples per channel.
        expected_min = int(0.5 * tc_config.rate_hz * duration_s)
        assert total_samples >= expected_min, (
            f"TDMS contains {total_samples} samples; expected >= {expected_min}"
        )


# ---------------------------------------------------------------------------
# C2 — LoggingMode.LOG_AND_READ: both paths receive samples
# ---------------------------------------------------------------------------


async def test_c2_tdms_log_and_read_dual_path(
    tc_config: TcHardwareConfig,
    tc_spec_continuous: TaskSpec,
    hw_tmp_dir: Path,
) -> None:
    """``LOG_AND_READ`` writes the TDMS file *and* delivers blocks to the recorder."""
    from nidaqmx.constants import LoggingMode

    tdms_file = _tdms_file_factory()
    tdms_path = hw_tmp_dir / "c2.tdms"
    spec = tc_spec_continuous.replace(
        logging=TdmsLogging(path=tdms_path, mode=LoggingMode.LOG_AND_READ)
    )
    chunk_size = max(2, int(tc_config.rate_hz // 2))
    target_blocks = 3

    seen: list[DaqBlock] = []
    async with (
        open_task(spec) as session,
        record(session, chunk_size=chunk_size) as (rx, summary),
    ):
        async for block in rx:
            seen.append(block)
            if len(seen) >= target_blocks:
                break

    assert len(seen) == target_blocks
    assert summary.blocks_emitted == target_blocks

    with tdms_file.open(tdms_path) as tdms:
        groups = tdms.groups()
        assert groups, "TDMS file contains no groups"
        total_samples = sum(len(ch) for group in groups for ch in group.channels())

    # The TDMS file should carry at least the samples the recorder saw —
    # NI may continue writing after the recorder context exits, so the
    # lower bound is what matters.
    expected_min = chunk_size * target_blocks
    assert total_samples >= expected_min, (
        f"TDMS samples ({total_samples}) < recorder samples ({expected_min})"
    )
