"""SQLite sink — stdlib :mod:`sqlite3` + WAL, parameterised ``executemany``.

Accepts :class:`DaqReading` and :class:`DaqSample` via :meth:`write_many`,
and :class:`DaqBlock` via :meth:`write` — one row per block (summary, no
scalarisation, design doc §14 table). Different shapes go to different
tables: ``readings`` / ``samples`` / ``blocks`` by default. Override via
the ``table_*`` arguments.

The ``sqlite3`` driver is synchronous; calls go through
:func:`anyio.to_thread.run_sync` so the event loop stays responsive.

Best-practice defaults:

- ``journal_mode=WAL`` + ``synchronous=NORMAL``.
- ``busy_timeout=5000`` ms.
- One ``BEGIN IMMEDIATE`` … ``COMMIT`` per ``write_many`` / ``write``.
- SQL identifiers validated against ``^[A-Za-z_][A-Za-z0-9_]{0,62}$``;
  values always parameterised.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from anyio.to_thread import run_sync

from nidaqlib._logging import get_logger
from nidaqlib.errors import NIDaqSinkWriteError
from nidaqlib.sinks._schema import ColumnSpec, SchemaLock
from nidaqlib.sinks.base import reading_to_row, sample_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from nidaqlib.tasks.models import DaqBlock, DaqReading, DaqSample


__all__ = ["SqliteSink"]


_logger = get_logger("sinks.sqlite")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_JournalMode = Literal["WAL", "DELETE", "MEMORY", "TRUNCATE", "PERSIST", "OFF"]
_Synchronous = Literal["FULL", "NORMAL", "OFF", "EXTRA"]


def _validate_identifier(name: str, *, label: str) -> str:
    """Return ``name`` if it is a safe SQL identifier; raise otherwise."""
    if not _IDENTIFIER_PATTERN.fullmatch(name):
        msg = (
            f"{label} must match [A-Za-z_][A-Za-z0-9_]{{0,62}}; got {name!r}. "
            "Table names are interpolated into CREATE/INSERT statements, so "
            "they must be safe identifiers."
        )
        raise ValueError(msg)
    return name


def _column_type(spec: ColumnSpec) -> str:
    """Map a :class:`ColumnSpec` to a SQLite type affinity."""
    if spec.python_type is float:
        return "REAL"
    if spec.python_type in (int, bool):
        return "INTEGER"
    return "TEXT"


def _quote_identifier(name: str) -> str:
    """Return ``name`` as a safely quoted SQLite identifier."""
    return '"' + name.replace('"', '""') + '"'


def _block_summary_row(block: DaqBlock) -> dict[str, float | int | str | bool | None]:
    """Flatten a :class:`DaqBlock` into one summary row.

    No scalarisation — one row per block. Carries the block-level provenance
    fields (``block_index``, ``first_sample_index``, ``samples_per_channel``,
    ``sample_rate_hz``, ``task_started_at``, ``t0``, etc.) plus the raw
    channel list and units. The ``data`` array is intentionally **not**
    serialised; consumers who need samples should use :class:`ParquetSink`
    or scalarise explicitly via :func:`block_to_long_rows`.
    """
    return {
        "device": block.device,
        "task": block.task,
        "channels": ",".join(block.channels),
        "block_index": block.block_index,
        "first_sample_index": block.first_sample_index,
        "samples_per_channel": block.samples_per_channel,
        "sample_rate_hz": block.sample_rate_hz,
        "dt_s": block.dt_s,
        "task_started_at": block.task_started_at.isoformat(),
        "t0": block.t0.isoformat(),
        "monotonic_ns": block.monotonic_ns,
        "read_started_at": block.read_started_at.isoformat(),
        "read_finished_at": block.read_finished_at.isoformat(),
        "elapsed_s": block.elapsed_s,
        "error_type": (
            f"{type(block.error).__module__}.{type(block.error).__qualname__}"
            if block.error is not None
            else None
        ),
        "error_message": str(block.error) if block.error is not None else None,
    }


class SqliteSink:
    """Append-only SQLite writer with WAL journaling and per-table schema lock.

    One sink instance routes records to up to three tables, one per shape
    (readings / samples / blocks). Each table's column set is locked on its
    first write; later writes project onto the locked schema and drop
    unknown columns with a one-shot WARN.

    Args:
        path: Destination SQLite file.
        table_readings: Table name for :class:`DaqReading` rows.
        table_samples: Table name for :class:`DaqSample` rows.
        table_blocks: Table name for :class:`DaqBlock` summary rows.
        journal_mode: SQLite journal mode pragma. ``WAL`` is recommended.
        synchronous: SQLite ``synchronous`` pragma. ``NORMAL`` balances
            durability and throughput.
        busy_timeout_ms: SQLite busy-wait, in milliseconds.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        table_readings: str = "readings",
        table_samples: str = "samples",
        table_blocks: str = "blocks",
        journal_mode: _JournalMode = "WAL",
        synchronous: _Synchronous = "NORMAL",
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._path = Path(path)
        self._table_readings = _validate_identifier(table_readings, label="table_readings")
        self._table_samples = _validate_identifier(table_samples, label="table_samples")
        self._table_blocks = _validate_identifier(table_blocks, label="table_blocks")
        self._journal_mode: _JournalMode = journal_mode
        self._synchronous: _Synchronous = synchronous
        if busy_timeout_ms < 0:
            raise ValueError(f"busy_timeout_ms must be >= 0, got {busy_timeout_ms!r}")
        self._busy_timeout_ms = busy_timeout_ms
        self._conn: sqlite3.Connection | None = None
        # Per-table schema state. One :class:`SchemaLock` per table because
        # each carries a different row shape (readings / samples / blocks).
        self._lock_readings = SchemaLock(sink_name="sqlite.readings", logger=_logger)
        self._lock_samples = SchemaLock(sink_name="sqlite.samples", logger=_logger)
        self._lock_blocks = SchemaLock(sink_name="sqlite.blocks", logger=_logger)
        self._insert_readings: str | None = None
        self._insert_samples: str | None = None
        self._insert_blocks: str | None = None

    @property
    def path(self) -> Path:
        """Destination SQLite file path."""
        return self._path

    async def open(self) -> None:
        """Open the SQLite connection and apply pragmas."""
        if self._conn is not None:
            return
        self._conn = await run_sync(self._connect_blocking)
        _logger.info(
            "sinks.sqlite.open",
            extra={"path": str(self._path), "journal_mode": self._journal_mode},
        )

    def _connect_blocking(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self._path),
            isolation_level=None,
            check_same_thread=False,
        )
        conn.execute(f"PRAGMA journal_mode = {self._journal_mode}")
        conn.execute(f"PRAGMA synchronous = {self._synchronous}")
        conn.execute(f"PRAGMA busy_timeout = {int(self._busy_timeout_ms)}")
        return conn

    async def write_many(
        self,
        items: Sequence[DaqReading] | Sequence[DaqSample],
    ) -> None:
        """Append :class:`DaqReading` or :class:`DaqSample` rows."""
        if self._conn is None:
            raise RuntimeError("SqliteSink: write_many called before open()")
        if not items:
            return

        from nidaqlib.tasks.models import DaqReading, DaqSample  # noqa: PLC0415

        first = items[0]
        if isinstance(first, DaqReading):
            rows = [reading_to_row(r) for r in items]  # type: ignore[arg-type]
            await self._insert_rows(
                rows, table=self._table_readings, lock=self._lock_readings, kind="readings"
            )
        elif isinstance(first, DaqSample):  # pyright: ignore[reportUnnecessaryIsInstance]
            rows = [sample_to_row(s) for s in items]  # type: ignore[arg-type]
            await self._insert_rows(
                rows, table=self._table_samples, lock=self._lock_samples, kind="samples"
            )
        else:  # pragma: no cover - defensive
            raise NIDaqSinkWriteError(
                f"SqliteSink.write_many: unsupported record type {type(first).__name__}"
            )

    async def write(self, block: DaqBlock) -> None:
        """Append one :class:`DaqBlock` as a summary row."""
        if self._conn is None:
            raise RuntimeError("SqliteSink: write called before open()")
        await self._insert_rows(
            [_block_summary_row(block)],
            table=self._table_blocks,
            lock=self._lock_blocks,
            kind="blocks",
        )

    async def _insert_rows(
        self,
        rows: list[dict[str, float | int | str | bool | None]],
        *,
        table: str,
        lock: SchemaLock,
        kind: str,
    ) -> None:
        if not rows:
            return
        if not lock.is_locked:
            lock.lock(rows)
            await run_sync(self._create_table_blocking, table, lock)
            self._set_insert_sql(kind, self._build_insert_sql(table, lock))
        insert_sql = self._get_insert_sql(kind)
        assert insert_sql is not None  # noqa: S101
        columns = lock.columns
        assert columns is not None  # noqa: S101
        projected: list[tuple[object, ...]] = []
        for row in rows:
            fields = lock.project(row)
            projected.append(tuple(fields[spec.name] for spec in columns))
        await run_sync(self._executemany_blocking, insert_sql, projected)

    def _set_insert_sql(self, kind: str, sql: str) -> None:
        if kind == "readings":
            self._insert_readings = sql
        elif kind == "samples":
            self._insert_samples = sql
        else:
            self._insert_blocks = sql

    def _get_insert_sql(self, kind: str) -> str | None:
        if kind == "readings":
            return self._insert_readings
        if kind == "samples":
            return self._insert_samples
        return self._insert_blocks

    def _build_insert_sql(self, table: str, lock: SchemaLock) -> str:
        columns = lock.columns
        assert columns is not None  # noqa: S101
        col_list = ", ".join(_quote_identifier(spec.name) for spec in columns)
        placeholders = ", ".join("?" for _ in columns)
        # S608: identifiers validated in __init__; values parameterised.
        return f"INSERT INTO {_quote_identifier(table)} ({col_list}) VALUES ({placeholders})"  # noqa: S608

    def _create_table_blocking(self, table: str, lock: SchemaLock) -> None:
        assert self._conn is not None  # noqa: S101
        columns = lock.columns
        assert columns is not None  # noqa: S101
        col_defs = ", ".join(
            f"{_quote_identifier(spec.name)} {_column_type(spec)}" for spec in columns
        )
        stmt = f"CREATE TABLE IF NOT EXISTS {_quote_identifier(table)} ({col_defs})"
        try:
            self._conn.execute(stmt)
        except sqlite3.Error as exc:
            raise NIDaqSinkWriteError(
                f"SqliteSink: CREATE TABLE failed for {table!r}: {exc}"
            ) from exc

    def _executemany_blocking(
        self,
        insert_sql: str,
        rows: Sequence[tuple[object, ...]],
    ) -> None:
        assert self._conn is not None  # noqa: S101
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.executemany(insert_sql, rows)
            self._conn.execute("COMMIT")
        except sqlite3.Error as exc:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                _logger.exception("sinks.sqlite.rollback_failed")
            raise NIDaqSinkWriteError(f"SqliteSink: INSERT failed: {exc}") from exc

    async def close(self) -> None:
        """Close the connection. Idempotent."""
        if self._conn is None:
            return
        conn = self._conn
        self._conn = None
        try:
            await run_sync(conn.close)
        finally:
            _logger.info("sinks.sqlite.close", extra={"path": str(self._path)})

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        await self.close()
