"""``nidaq-list`` — print devices and physical channels (design doc §20.1).

Usage::

    nidaq-list             # all devices
    nidaq-list Dev1        # AI channels on Dev1
    nidaq-list --json      # machine-readable form
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from nidaqlib.errors import NIDaqError
from nidaqlib.system import find_devices, list_physical_channels

if TYPE_CHECKING:
    from collections.abc import Sequence

    from nidaqlib.system.models import DeviceInfo


__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nidaq-list",
        description="List visible NI DAQ devices and their physical channels.",
    )
    parser.add_argument(
        "device",
        nargs="?",
        default=None,
        help="Optional device name (e.g. Dev1). Without it, all devices are listed.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text.",
    )
    return parser


def _device_to_dict(info: DeviceInfo) -> dict[str, object]:
    return {
        "name": info.name,
        "product_type": info.product_type,
        "serial_number": info.serial_number,
        "ai_physical_channels": list(info.ai_physical_channels),
        "ao_physical_channels": list(info.ao_physical_channels),
        "di_lines": list(info.di_lines),
        "do_lines": list(info.do_lines),
        "ci_physical_channels": list(info.ci_physical_channels),
        "co_physical_channels": list(info.co_physical_channels),
    }


def _print_human(devices: list[DeviceInfo]) -> None:
    if not devices:
        print("No NI devices found.")
        return
    for info in devices:
        product = info.product_type or "<unknown>"
        serial = info.serial_number or "<unknown>"
        print(f"{info.name}  ({product}, S/N {serial})")
        for label, names in (
            ("AI", info.ai_physical_channels),
            ("AO", info.ao_physical_channels),
            ("DI", info.di_lines),
            ("DO", info.do_lines),
            ("CI", info.ci_physical_channels),
            ("CO", info.co_physical_channels),
        ):
            if names:
                print(f"  {label}: {', '.join(names)}")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point — `print -> exit code` so console scripts work cleanly."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.device is not None:
            channels = list_physical_channels(args.device)
            if args.json:
                json.dump({"device": args.device, "ai_channels": list(channels)}, sys.stdout)
                sys.stdout.write("\n")
            elif not channels:
                print(f"{args.device}: no AI physical channels found")
            else:
                print(f"{args.device}:")
                for ch in channels:
                    print(f"  {ch}")
            return 0
        results = find_devices()
        # Surface enumeration failures (driver missing, system call exploded)
        # so the user sees a clear reason rather than "no devices found".
        for row in results:
            if not row.ok and row.error is not None:
                print(f"nidaq-list: {row.error}", file=sys.stderr)
                return 1
        devices = [row.device_info for row in results if row.ok and row.device_info is not None]
        if args.json:
            json.dump([_device_to_dict(d) for d in devices], sys.stdout)
            sys.stdout.write("\n")
        else:
            _print_human(devices)
        return 0
    except NIDaqError as exc:
        print(f"nidaq-list: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
