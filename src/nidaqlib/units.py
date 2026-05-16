""":func:`to_pint` — best-effort NI-unit → pint-compatible string conversion.

Pint is **not** a runtime dependency of nidaqlib. This module returns plain
strings that pint accepts (``"degC"``, ``"V"``, ``"K"``, ...) so downstream
consumers who do use pint can parse them via ``pint.UnitRegistry().Unit()``.

Lossy by design — same rule as the sibling libraries. ``None`` means "no
mapping known"; callers should treat that as a passthrough hint rather
than an error.
"""

from __future__ import annotations

from typing import Any

__all__ = ["to_pint"]


def _temperature_table() -> dict[Any, str]:
    """Lazy-load the ``TemperatureUnits`` enum → pint mapping.

    Deferred import so callers without ``nidaqmx`` installed (notebooks,
    sibling-API symmetry tests) can still import :mod:`nidaqlib.units`.
    """
    try:
        from nidaqmx.constants import TemperatureUnits  # noqa: PLC0415
    except ImportError:  # pragma: no cover — nidaqmx is a required dep in practice
        return {}
    return {
        TemperatureUnits.DEG_C: "degC",
        TemperatureUnits.DEG_F: "degF",
        TemperatureUnits.K: "K",
        TemperatureUnits.DEG_R: "degR",
    }


# Plain-string passthrough table. The NI-driver-name on the left, the
# pint-canonical string on the right. Same values nidaqmx.constants emits
# via .value for the matching enum members.
_STRING_PASSTHROUGH: dict[str, str] = {
    "degC": "degC",
    "degF": "degF",
    "K": "K",
    "degR": "degR",
    "V": "V",
    "mV": "mV",
    "A": "A",
    "mA": "mA",
    "Hz": "Hz",
    "kHz": "kHz",
    "MHz": "MHz",
    "Pa": "Pa",
    "kPa": "kPa",
    "psi": "psi",
    "g": "g",  # standard gravities; pint treats as "gravity" when contextual
}


def to_pint(unit: object) -> str | None:
    """Return a pint-compatible unit string for ``unit``, or ``None``.

    Accepts:
        - ``None`` → ``None``.
        - A string already in pint form (``"degC"``, ``"V"``, ...) — passed
          through unchanged when it's in the known set; otherwise returned
          as-is so unfamiliar units don't get silently dropped.
        - An ``nidaqmx.constants.TemperatureUnits`` member → mapped through
          the dedicated temperature table.

    Lossy by design: no tuple, no discriminator, no exception on unknown
    units — same contract as the sibling libraries.
    """
    if unit is None:
        return None
    if isinstance(unit, str):
        return _STRING_PASSTHROUGH.get(unit, unit)
    # Anything else — try the TemperatureUnits enum table. The lazy import
    # keeps this module callable when nidaqmx is absent.
    temperature = _temperature_table()
    if unit in temperature:
        return temperature[unit]
    # Last-ditch: many NI enums expose .name as a SHOUTING string. We don't
    # try to be clever; let callers handle the unknown case.
    name = getattr(unit, "name", None)
    if isinstance(name, str):
        return _STRING_PASSTHROUGH.get(name)
    return None
