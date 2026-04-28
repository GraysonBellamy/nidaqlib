"""Shared first-batch schema-lock for tabular sinks.

Direct port of sartoriuslib's ``sinks/_schema.py``. Every tabular sink
in the tree (SQLite, Parquet) shares the same schema-evolution policy:

1. **First batch wins.** The column set and order are locked from the
   first :meth:`write_many`/``write`` call.
2. **Unknown columns are dropped with a one-shot WARN.**
3. **Missing columns are filled with ``None``.**

This module is sink-facing only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping, Sequence

__all__ = ["ColumnSpec", "SchemaLock"]


_SCALAR_TYPE = type[float] | type[int] | type[str] | type[bool]


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One column in a locked tabular schema."""

    name: str
    python_type: _SCALAR_TYPE
    nullable: bool


class SchemaLock:
    """Lock a row-dict schema on first batch; drop unknowns on later batches."""

    def __init__(self, *, sink_name: str, logger: logging.Logger) -> None:
        self._sink_name = sink_name
        self._logger = logger
        self._columns: tuple[ColumnSpec, ...] | None = None
        self._names: frozenset[str] = frozenset()
        self._unknown_warned: set[str] = set()

    @property
    def columns(self) -> tuple[ColumnSpec, ...] | None:
        """Locked columns in declaration order, or ``None`` before lock."""
        return self._columns

    @property
    def is_locked(self) -> bool:
        """``True`` once :meth:`lock` or :meth:`lock_to` has been called."""
        return self._columns is not None

    def lock(self, rows: Sequence[Mapping[str, object]]) -> tuple[ColumnSpec, ...]:
        """Infer column specs from ``rows`` and lock the schema."""
        if self._columns is not None:
            raise RuntimeError("SchemaLock.lock called twice")
        if not rows:
            raise ValueError("SchemaLock.lock requires a non-empty first batch")

        ordered_keys: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    ordered_keys.append(key)
                    seen.add(key)

        specs = [self._infer_column(key, rows) for key in ordered_keys]
        self._columns = tuple(specs)
        self._names = frozenset(ordered_keys)
        return self._columns

    @staticmethod
    def _infer_column(key: str, rows: Sequence[Mapping[str, object]]) -> ColumnSpec:
        """Infer one column's spec from the first batch."""
        inferred: type | None = None
        nullable = False
        for row in rows:
            if key not in row:
                nullable = True
                continue
            value = row[key]
            if value is None:
                nullable = True
                continue
            value_type = type(value)
            if inferred is None:
                inferred = value_type
            elif inferred is not value_type:
                # int + float → float; bool + int → int (bool is subtype);
                # any other mix → str.
                pair = {inferred, value_type}
                if pair <= {int, float}:
                    inferred = float
                elif pair <= {int, bool}:
                    inferred = int
                else:
                    inferred = str
        if inferred is None:
            inferred = str
            nullable = True
        elif inferred not in (float, int, str, bool):
            inferred = str
        return ColumnSpec(name=key, python_type=inferred, nullable=nullable)

    def lock_to(self, specs: Sequence[ColumnSpec]) -> tuple[ColumnSpec, ...]:
        """Lock the schema from an externally-supplied spec list."""
        if self._columns is not None:
            raise RuntimeError("SchemaLock.lock_to called twice")
        if not specs:
            raise ValueError("SchemaLock.lock_to requires at least one column")
        self._columns = tuple(specs)
        self._names = frozenset(spec.name for spec in self._columns)
        return self._columns

    def project(self, row: Mapping[str, object]) -> dict[str, object]:
        """Return a new dict containing only keys from the locked schema."""
        if self._columns is None:
            raise RuntimeError("SchemaLock.project called before lock()")

        result: dict[str, object] = {spec.name: None for spec in self._columns}
        for key, value in row.items():
            if key in self._names:
                result[key] = value
                continue
            if key not in self._unknown_warned:
                self._unknown_warned.add(key)
                self._logger.warning(
                    "sink.unknown_column_dropped",
                    extra={"sink": self._sink_name, "column": key},
                )
        return result
