"""``nidaq-info`` — print driver / device / library info (design doc §20.4).

Run ``nidaq-info`` for a human-readable summary, or ``nidaq-info --json``
for a machine-readable form. Useful for bug reports and quick environment
checks. Mirrors the ``alicat-info`` / ``sartorius-info`` shape from the
sibling packages.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from typing import TYPE_CHECKING, Any

from nidaqlib import __version__
from nidaqlib.errors import NIDaqDependencyError, NIDaqError
from nidaqlib.system import list_devices

if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nidaq-info",
        description="Print nidaqlib, NI driver, and device information.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text.",
    )
    return parser


def _gather() -> dict[str, Any]:
    info: dict[str, Any] = {
        "nidaqlib_version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    nidaqmx_version: str | None
    try:
        import nidaqmx  # noqa: PLC0415
    except ImportError:
        nidaqmx_version = None
    else:
        nidaqmx_version = getattr(nidaqmx, "__version__", None)
    info["nidaqmx_version"] = nidaqmx_version

    driver_version: str | None = None
    try:
        import nidaqmx.system  # noqa: PLC0415

        sysobj = nidaqmx.system.System.local()
        major = getattr(sysobj, "driver_version", None)
        # Older nidaqmx-python exposes a tuple-shaped DriverVersion namedtuple.
        if major is not None:
            driver_version = ".".join(str(v) for v in major if v is not None)
    except Exception:  # pragma: no cover - defensive
        driver_version = None
    info["ni_driver_version"] = driver_version

    devices: list[dict[str, Any]]
    try:
        devices = [
            {
                "name": d.name,
                "product_type": d.product_type,
                "serial_number": d.serial_number,
                "ai_count": len(d.ai_physical_channels),
                "ao_count": len(d.ao_physical_channels),
                "di_count": len(d.di_lines),
                "do_count": len(d.do_lines),
            }
            for d in list_devices()
        ]
    except NIDaqDependencyError:
        devices = []
    info["devices"] = devices
    return info


def _print_human(info: dict[str, Any]) -> None:
    print(f"nidaqlib       {info['nidaqlib_version']}")
    print(f"python         {info['python']}")
    print(f"platform       {info['platform']}")
    print(f"nidaqmx        {info['nidaqmx_version'] or '<not installed>'}")
    print(f"NI driver      {info['ni_driver_version'] or '<unavailable>'}")
    devices = info["devices"]
    if not devices:
        print("devices        none visible")
        return
    print("devices:")
    for d in devices:
        product = d["product_type"] or "<unknown>"
        serial = d["serial_number"] or "<unknown>"
        print(
            f"  {d['name']:<8}  {product:<24}  S/N {serial}"
            f"  AI={d['ai_count']:<3}  AO={d['ao_count']:<3}"
            f"  DI={d['di_count']:<3}  DO={d['do_count']:<3}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        info = _gather()
    except NIDaqError as exc:
        print(f"nidaq-info: {exc}", file=sys.stderr)
        return 1
    if args.json:
        json.dump(info, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_human(info)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
