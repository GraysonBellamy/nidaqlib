"""``nidaq-capture`` — short hardware-clocked acquisition to file (design doc §20.3).

Usage::

    nidaq-capture Dev1/ai0 Dev1/ai1 --rate 1000 --duration 10 --out run.parquet
    nidaq-capture Dev1/ai0 --rate 5000 --duration 2 --out run.tdms

Output format is inferred from the file extension:

- ``.parquet`` → :class:`ParquetSink` (preferred for high-rate blocks).
- ``.tdms``    → driver-side TDMS via :class:`TdmsLogging` (no app-side
  sink; the recorder runs in :attr:`LoggingMode.LOG` mode).

CSV/JSONL outputs are intentionally not offered here — they refuse blocks
by default for a reason. Use ``ParquetSink`` for blocks; use
``record_polled`` programmatically for low-rate scalar logging.
"""

from __future__ import annotations

import argparse
import sys
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from nidaqlib import (
    AcquisitionMode,
    AnalogInputVoltage,
    NIDaqError,
    TaskSpec,
    Timing,
    open_task,
)
from nidaqlib.sinks import ParquetSink
from nidaqlib.streaming import record
from nidaqlib.tasks.spec import TdmsLogging

if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nidaq-capture",
        description="Run a short hardware-clocked acquisition to a Parquet or TDMS file.",
    )
    parser.add_argument(
        "channels",
        nargs="+",
        help="One or more NI physical channels (e.g. Dev1/ai0 Dev1/ai1).",
    )
    parser.add_argument("--rate", type=float, required=True, help="Sample rate, Hz.")
    parser.add_argument(
        "--duration",
        type=float,
        required=True,
        help="Total acquisition duration, seconds.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output file path. Extension chooses the format (.parquet or .tdms).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="Samples per channel per block. Defaults to one second of data.",
    )
    parser.add_argument(
        "--min",
        dest="min_val",
        type=float,
        default=-10.0,
        help="Lower input range, volts (default -10).",
    )
    parser.add_argument(
        "--max",
        dest="max_val",
        type=float,
        default=10.0,
        help="Upper input range, volts (default +10).",
    )
    parser.add_argument(
        "--task-name",
        type=str,
        default="nidaq-capture",
        help="Task name (labels emitted records).",
    )
    return parser


async def _capture_parquet(
    spec: TaskSpec,
    *,
    duration: float,
    chunk_size: int,
    out: Path,
) -> int:
    """Hardware-clocked capture into a Parquet file. Returns blocks written."""
    blocks_target = max(1, int(duration * spec.timing.rate_hz / chunk_size))  # type: ignore[union-attr]
    written = 0
    async with (
        open_task(spec) as session,
        record(session, chunk_size=chunk_size) as (rx, _summary),
        ParquetSink(out) as sink,
    ):
        count = 0

        async def _bounded_pipe() -> None:
            nonlocal count
            async for block in rx:
                await sink.write(block)
                count += 1
                if count >= blocks_target:
                    return

        await _bounded_pipe()
        written = count
    return written


async def _capture_tdms(
    spec_template: TaskSpec,
    *,
    duration: float,
    out: Path,
) -> None:
    """Driver-side TDMS capture — no application-side sink."""
    from nidaqmx.constants import LoggingMode  # noqa: PLC0415

    spec = spec_template.replace(logging=TdmsLogging(path=out, mode=LoggingMode.LOG))
    # LOG-only — the recorder short-circuits to an empty stream; samples flow
    # into the TDMS file via the driver. Sleep for the configured duration to
    # keep the task running.
    async with (
        open_task(spec) as session,
        record(session, chunk_size=1) as (_rx, _summary),
    ):
        # `session` is used to construct the recorder context; once we're
        # inside, we just sleep — samples flow into the TDMS file via NI.
        _ = session
        await anyio.sleep(duration)


def _build_spec(args: argparse.Namespace) -> TaskSpec:
    channels = [
        AnalogInputVoltage(
            physical_channel=ch,
            min_val=args.min_val,
            max_val=args.max_val,
        )
        for ch in args.channels
    ]
    return TaskSpec(
        name=args.task_name,
        channels=channels,
        timing=Timing(rate_hz=args.rate, mode=AcquisitionMode.CONTINUOUS),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.rate <= 0:
        print("nidaq-capture: --rate must be > 0", file=sys.stderr)
        return 2
    if args.duration <= 0:
        print("nidaq-capture: --duration must be > 0", file=sys.stderr)
        return 2

    chunk_size = args.chunk_size or max(1, int(args.rate))
    spec = _build_spec(args)
    suffix = args.out.suffix.lower()

    try:
        if suffix == ".parquet":
            written = anyio.run(
                partial(
                    _capture_parquet,
                    spec,
                    duration=args.duration,
                    chunk_size=chunk_size,
                    out=args.out,
                )
            )
            print(f"nidaq-capture: wrote {written} blocks to {args.out}")
            return 0
        if suffix == ".tdms":
            anyio.run(partial(_capture_tdms, spec, duration=args.duration, out=args.out))
            print(f"nidaq-capture: TDMS file written to {args.out}")
            return 0
        print(
            f"nidaq-capture: unknown output extension {suffix!r}; use .parquet or .tdms",
            file=sys.stderr,
        )
        return 2
    except NIDaqError as exc:
        print(f"nidaq-capture: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
