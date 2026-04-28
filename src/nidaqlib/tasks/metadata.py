"""Run metadata + TDMS sidecar serialisation (design doc §18.2 / §18.4).

A :class:`RunMetadata` captures the full provenance of an acquisition —
the spec of every task, the library / driver / interpreter versions, the
operator-supplied free-form payload — and is serialisable to a sidecar
JSON file that travels next to a TDMS file.

Sidecar layout:

::

    run.tdms
    run.metadata.json

The sidecar is *opt-in*; nothing in the streaming or session layers
writes it implicitly. Callers pass a :class:`RunMetadata` to
:func:`write_sidecar` (or wire it into their own recorder loop) when they
want the provenance trail.
"""

from __future__ import annotations

import dataclasses
import json
import platform
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from nidaqlib.errors import NIDaqValidationError
from nidaqlib.tasks.spec import TaskSpec
from nidaqlib.version import __version__


def _empty_user_metadata() -> dict[str, object]:
    return {}


def _empty_task_specs() -> dict[str, TaskSpec]:
    return {}


def _detect_nidaqmx_version() -> str:
    """Return the installed ``nidaqmx-python`` version, or ``"unknown"``.

    Resolved lazily — the metadata layer must remain importable on systems
    that do not have the NI driver installed (CI, fake-backend tests).
    """
    try:
        import nidaqmx  # noqa: PLC0415
    except ImportError:
        return "unknown"
    return str(getattr(nidaqmx, "__version__", "unknown"))


def _detect_ni_driver_version() -> str | None:
    """Return the NI-DAQmx driver version, or ``None`` when unavailable.

    Uses ``nidaqmx.system.System.local().driver_version`` — a structured
    object on real hardware. Returns ``None`` when ``nidaqmx`` is missing
    or the system query raises (e.g., no driver, no devices).
    """
    try:
        from nidaqmx.system import System  # noqa: PLC0415
    except ImportError:
        return None
    try:
        version = System.local().driver_version
    except Exception:
        return None
    # ``driver_version`` is a ``DriverVersion`` named tuple of three ints.
    parts = (
        getattr(version, "major_version", None),
        getattr(version, "minor_version", None),
        getattr(version, "update_version", None),
    )
    if all(p is not None for p in parts):
        return ".".join(str(p) for p in parts)
    return str(version)


@dataclass(frozen=True, slots=True, kw_only=True)
class RunMetadata:
    """Provenance bundle for one acquisition run (design doc §18.2).

    Attributes:
        run_id: Caller-chosen identifier for the run (e.g. UUID, ISO
            timestamp, experiment name). Must be unique within the
            caller's storage scheme — :mod:`nidaqlib` does not enforce
            uniqueness.
        started_at: Wall-clock timestamp at which the run began. UTC.
        nidaqlib_version: Version of this package.
        nidaqmx_python_version: Version of the ``nidaqmx-python`` binding.
        ni_driver_version: NI-DAQmx driver version, or ``None`` if the
            driver is not installed (e.g., CI environment).
        python_version: Runtime Python version string.
        platform: Platform string from :func:`platform.platform`.
        task_specs: One :class:`TaskSpec` per task in the run, keyed by
            the manager-add name.
        user_metadata: Free-form mapping the operator wants persisted
            alongside the run (git commit, sample ID, recipe name, ...).
            Values must be JSON-serialisable.
    """

    run_id: str
    started_at: datetime
    nidaqlib_version: str = field(default_factory=lambda: __version__)
    nidaqmx_python_version: str = field(default_factory=_detect_nidaqmx_version)
    ni_driver_version: str | None = field(default_factory=_detect_ni_driver_version)
    python_version: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=platform.platform)
    task_specs: Mapping[str, TaskSpec] = field(default_factory=_empty_task_specs)
    user_metadata: Mapping[str, object] = field(default_factory=_empty_user_metadata)

    @classmethod
    def for_run(
        cls,
        run_id: str,
        *,
        task_specs: Mapping[str, TaskSpec] | None = None,
        user_metadata: Mapping[str, object] | None = None,
        started_at: datetime | None = None,
    ) -> Self:
        """Construct a :class:`RunMetadata` with auto-detected versions.

        Convenience wrapper around the dataclass constructor that supplies
        the version / platform / timestamp defaults so callers only need to
        pass the run-specific fields.
        """
        return cls(
            run_id=run_id,
            started_at=started_at if started_at is not None else datetime.now(UTC),
            task_specs=dict(task_specs) if task_specs is not None else {},
            user_metadata=dict(user_metadata) if user_metadata is not None else {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict.

        ``task_specs`` round-trips through :meth:`TaskSpec.to_dict`, which
        in turn dispatches each channel and trigger by ``kind``.
        """
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "nidaqlib_version": self.nidaqlib_version,
            "nidaqmx_python_version": self.nidaqmx_python_version,
            "ni_driver_version": self.ni_driver_version,
            "python_version": self.python_version,
            "platform": self.platform,
            "task_specs": {name: spec.to_dict() for name, spec in self.task_specs.items()},
            "user_metadata": dict(self.user_metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialise from a dict produced by :meth:`to_dict`.

        Raises:
            NIDaqValidationError: A required field is missing or malformed.
        """
        required = ("run_id", "started_at")
        for key in required:
            if key not in data:
                raise NIDaqValidationError(f"RunMetadata missing required field {key!r}")
        try:
            started_at = datetime.fromisoformat(str(data["started_at"]))
        except ValueError as exc:
            raise NIDaqValidationError(
                f"RunMetadata.started_at must be ISO-8601, got {data['started_at']!r}"
            ) from exc
        raw_specs = data.get("task_specs", {})
        if not isinstance(raw_specs, Mapping):
            raise NIDaqValidationError(
                f"RunMetadata.task_specs must be a mapping, got {type(raw_specs).__name__}"
            )
        task_specs: dict[str, TaskSpec] = {}
        for name, payload in raw_specs.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(payload, Mapping):
                raise NIDaqValidationError(f"RunMetadata.task_specs[{name!r}] must be a mapping")
            task_specs[str(name)] = TaskSpec.from_dict(payload)  # pyright: ignore[reportUnknownArgumentType]
        user_metadata_raw = data.get("user_metadata", {})
        if not isinstance(user_metadata_raw, Mapping):
            raise NIDaqValidationError(
                f"RunMetadata.user_metadata must be a mapping, got "
                f"{type(user_metadata_raw).__name__}"
            )
        return cls(
            run_id=str(data["run_id"]),
            started_at=started_at,
            nidaqlib_version=str(data.get("nidaqlib_version", __version__)),
            nidaqmx_python_version=str(data.get("nidaqmx_python_version", "unknown")),
            ni_driver_version=(
                str(data["ni_driver_version"])
                if data.get("ni_driver_version") is not None
                else None
            ),
            python_version=str(data.get("python_version", sys.version.split()[0])),
            platform=str(data.get("platform", platform.platform())),
            task_specs=task_specs,
            user_metadata=dict(user_metadata_raw),  # pyright: ignore[reportUnknownArgumentType]
        )

    def replace(self, **updates: Any) -> Self:
        """Return a copy of this metadata with ``updates`` applied."""
        return dataclasses.replace(self, **updates)


def sidecar_path_for(tdms_path: str | Path) -> Path:
    """Return the conventional sidecar path for ``tdms_path``.

    ``run.tdms`` → ``run.metadata.json``. The ``.tdms`` suffix is replaced
    with ``.metadata.json``; any other extension gets ``.metadata.json``
    appended.
    """
    path = Path(tdms_path)
    if path.suffix.lower() == ".tdms":
        return path.with_suffix(".metadata.json")
    return path.with_name(path.name + ".metadata.json")


def write_sidecar(
    tdms_path: str | Path,
    metadata: RunMetadata,
    *,
    indent: int | None = 2,
) -> Path:
    """Write ``metadata`` next to ``tdms_path`` as ``<base>.metadata.json``.

    The TDMS file itself does not need to exist yet — the sidecar can be
    written before, during, or after the acquisition.

    Args:
        tdms_path: The TDMS file path. Determines the sidecar location via
            :func:`sidecar_path_for`.
        metadata: The :class:`RunMetadata` to serialise.
        indent: ``json.dumps`` indent parameter. ``None`` writes compactly.

    Returns:
        The :class:`pathlib.Path` of the written sidecar.
    """
    sidecar = sidecar_path_for(tdms_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(metadata.to_dict(), indent=indent), encoding="utf-8")
    return sidecar


def read_sidecar(tdms_path: str | Path) -> RunMetadata:
    """Read a sidecar adjacent to ``tdms_path`` and reconstruct a :class:`RunMetadata`.

    Raises:
        FileNotFoundError: The sidecar does not exist.
        NIDaqValidationError: The sidecar JSON is structurally invalid.
    """
    sidecar = sidecar_path_for(tdms_path)
    payload: object = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise NIDaqValidationError(
            f"sidecar {sidecar!s} must contain a JSON object, got {type(payload).__name__}"
        )
    return RunMetadata.from_dict(payload)  # pyright: ignore[reportUnknownArgumentType]


__all__ = [
    "RunMetadata",
    "read_sidecar",
    "sidecar_path_for",
    "write_sidecar",
]
