"""``nidaq-read`` — one-shot scalar read across one or more channels (design doc §20.2).

Usage::

    nidaq-read Dev1/ai0
    nidaq-read Dev1/ai0 Dev1/ai1 --json
    nidaq-read Dev1/ai0 --duration 10 --rate 5    # streamed for 10 s at 5 Hz

Without ``--duration``, performs one ``poll()`` and prints the result.
With ``--duration``, runs :func:`record_polled` for the given seconds at
``--rate`` Hz, streaming one row of output per tick.
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from typing import TYPE_CHECKING, cast

import anyio

from nidaqlib import (
    AnalogInputVoltage,
    NIDaqError,
    TaskSpec,
    open_task,
    record_polled,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from nidaqlib.tasks.models import DaqReading


__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nidaq-read",
        description="Read one or more analog-input channels (one-shot or streamed).",
    )
    parser.add_argument(
        "channels",
        nargs="+",
        help="One or more NI physical channels (e.g. Dev1/ai0).",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=1.0,
        help="Poll rate in Hz when streaming (default 1.0).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Stream for this many seconds. Without it, performs one poll.",
    )
    parser.add_argument(
        "--min", dest="min_val", type=float, default=-10.0, help="Lower input range, V."
    )
    parser.add_argument(
        "--max", dest="max_val", type=float, default=10.0, help="Upper input range, V."
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable rows."
    )
    parser.add_argument(
        "--task-name",
        type=str,
        default="nidaq-read",
        help="Task name (labels emitted records).",
    )
    return parser


def _build_spec(args: argparse.Namespace) -> TaskSpec:
    channels = [
        AnalogInputVoltage(
            physical_channel=ch,
            min_val=args.min_val,
            max_val=args.max_val,
        )
        for ch in args.channels
    ]
    # No Timing: poll() requires on-demand or no-clock tasks.
    return TaskSpec(name=args.task_name, channels=channels)


def _format_reading(reading: DaqReading, *, as_json: bool) -> str:
    if as_json:
        payload = {
            "device": reading.device,
            "task": reading.task,
            "midpoint_at": reading.midpoint_at.isoformat(),
            "monotonic_ns": reading.monotonic_ns,
            "elapsed_s": reading.elapsed_s,
            "values": dict(reading.values),
            "units": dict(reading.units),
        }
        return json.dumps(payload)
    parts = [f"{ch}={value!r}" for ch, value in reading.values.items()]
    return f"{reading.midpoint_at.isoformat()}  " + "  ".join(parts)


async def _one_shot(args: argparse.Namespace) -> int:
    spec = _build_spec(args)
    async with open_task(spec) as session:
        reading = await session.poll()
    print(_format_reading(reading, as_json=args.json))
    return 0


async def _streamed(args: argparse.Namespace) -> int:
    if args.rate <= 0:
        print("nidaq-read: --rate must be > 0", file=sys.stderr)
        return 2
    spec = _build_spec(args)
    deadline = anyio.current_time() + args.duration
    async with (
        open_task(spec) as session,
        record_polled(session, rate_hz=args.rate, buffer_size=32) as (rx, _summary),
    ):
        async for payload in rx:
            # Session-mode record_polled emits DaqReading. The Union return-shape
            # comes from the manager-mode overload, which we don't take here.
            reading = cast("DaqReading", payload)
            print(_format_reading(reading, as_json=args.json))
            if anyio.current_time() >= deadline:
                break
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.duration is None:
            return anyio.run(partial(_one_shot, args))
        return anyio.run(partial(_streamed, args))
    except NIDaqError as exc:
        print(f"nidaq-read: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
