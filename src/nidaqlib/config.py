"""Process-wide configuration for :mod:`nidaqlib`.

Plain frozen dataclass, no validation library — keeps the core install free
of optional deps. Env-var coercion lives in :func:`config_from_env`.

Design reference: ``docs/design.md`` §18.1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Final, Self

DEFAULT_ENV_PREFIX: Final[str] = "NIDAQLIB_"


@dataclass(frozen=True, slots=True, kw_only=True)
class NidaqConfig:
    """Process-wide default settings.

    Anything that varies *per task* (channel ranges, trigger source, TDMS
    path) belongs on :class:`~nidaqlib.tasks.TaskSpec`, not here.

    Attributes:
        default_timeout_s: Fallback NI read/write timeout, in seconds. Used
            when the call site does not supply one explicitly.
        default_sample_rate_hz: Fallback ``Timing.rate_hz`` when the
            :class:`~nidaqlib.tasks.Timing` field is unset.
        default_buffer_size: AnyIO send-stream capacity for ``record()``,
            measured in :class:`~nidaqlib.tasks.DaqBlock` slots.
        default_chunk_size: Samples per channel per emitted ``DaqBlock`` for
            ``record()``.
        eager_tasks: Opt-in to ``asyncio.eager_task_factory``. No-op on trio.
            See :func:`nidaqlib._runtime.install_eager_task_factory`.
    """

    default_timeout_s: float = 10.0
    default_sample_rate_hz: float = 1000.0
    default_buffer_size: int = 16
    default_chunk_size: int = 1000
    eager_tasks: bool = False

    def replace(self, **updates: Any) -> Self:
        """Return a copy of this config with ``updates`` applied."""
        return replace(self, **updates)


def config_from_env(prefix: str = DEFAULT_ENV_PREFIX) -> NidaqConfig:
    """Best-effort env loader.

    Only reads well-known keys. Missing or unparseable values fall back to
    :class:`NidaqConfig`'s defaults — this function never raises.

    Recognised keys (with ``prefix="NIDAQLIB_"``):

    - ``NIDAQLIB_DEFAULT_TIMEOUT_S`` — float seconds
    - ``NIDAQLIB_DEFAULT_SAMPLE_RATE_HZ`` — float Hz
    - ``NIDAQLIB_DEFAULT_BUFFER_SIZE`` — int slots
    - ``NIDAQLIB_DEFAULT_CHUNK_SIZE`` — int samples
    - ``NIDAQLIB_EAGER_TASKS`` — ``"1"`` / ``"true"`` / ``"yes"``

    Args:
        prefix: Prefix to prepend to each env key. Defaults to
            ``"NIDAQLIB_"``.

    Returns:
        A :class:`NidaqConfig` populated from env where parseable.
    """
    base = NidaqConfig()
    return NidaqConfig(
        default_timeout_s=_float_env(f"{prefix}DEFAULT_TIMEOUT_S", base.default_timeout_s),
        default_sample_rate_hz=_float_env(
            f"{prefix}DEFAULT_SAMPLE_RATE_HZ", base.default_sample_rate_hz
        ),
        default_buffer_size=_int_env(f"{prefix}DEFAULT_BUFFER_SIZE", base.default_buffer_size),
        default_chunk_size=_int_env(f"{prefix}DEFAULT_CHUNK_SIZE", base.default_chunk_size),
        eager_tasks=_bool_env(f"{prefix}EAGER_TASKS", base.eager_tasks),
    )


def _float_env(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


_TRUE_STRS: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_FALSE_STRS: Final[frozenset[str]] = frozenset({"0", "false", "no", "off", ""})


def _bool_env(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE_STRS:
        return True
    if lowered in _FALSE_STRS:
        return False
    return default


__all__ = ["DEFAULT_ENV_PREFIX", "NidaqConfig", "config_from_env"]
