"""PostgreSQL sink — :mod:`asyncpg`, COPY by default, parameterised fallback.

:class:`PostgresSink` writes :class:`DaqReading` rows via
:meth:`write_many`, and :class:`DaqBlock` summary rows via
:meth:`write` — one row per shape, routed to a per-shape table
(``readings`` / ``blocks`` by default). ``asyncpg`` is an
optional dependency behind ``nidaqlib[postgres]``; the import is
deferred to :meth:`open` so instantiation works on bare-core installs
and :class:`~nidaqlib.errors.NIDaqSinkDependencyError` is raised only
when the user actually tries to open a connection.

Best-practice defaults baked in:

- **Binary COPY** via :meth:`asyncpg.Connection.copy_records_to_table`.
  COPY is ~5-10x faster than parameterised INSERT for batches and is
  the recommended asyncpg bulk-ingest path. Callers that run on
  managed Postgres without COPY privileges can set
  :attr:`PostgresConfig.use_copy` to ``False`` to fall back to a
  prepared ``executemany``.
- **Connection pool** via :func:`asyncpg.create_pool`. The pool
  lifetime equals the sink lifetime; each batch acquires, writes,
  and releases.
- **Identifier validation** on ``schema`` and every table name (strict
  regex). Every value passes through ``$N`` placeholders — never
  string-formatted into SQL.
- **Credential scrubbing** — log lines that reference the connection
  use :meth:`PostgresConfig.target`, which never includes the
  password.
- **``statement_timeout``** applied as a server setting so a wedged
  query cannot block the acquisition loop forever.

Schema evolution mirrors the other tabular sinks. ``create_tables=False``
reads the target tables' columns from ``information_schema.columns`` on
open and locks each per-shape schema to that set. ``create_tables=True``
switches to first-batch inference and runs ``CREATE TABLE IF NOT EXISTS``
per shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import urlparse, urlunparse

from nidaqlib._logging import get_logger
from nidaqlib.errors import (
    NIDaqSinkDependencyError,
    NIDaqSinkSchemaError,
    NIDaqSinkWriteError,
)
from nidaqlib.sinks._schema import ColumnSpec, SchemaLock
from nidaqlib.sinks.base import reading_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from nidaqlib.tasks.models import DaqBlock, DaqReading

__all__ = ["PostgresConfig", "PostgresSink"]


_logger = get_logger("sinks.postgres")

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# Map PostgreSQL ``information_schema.columns.data_type`` values back onto
# Python scalar types. Anything not in this set degrades to ``str`` — the
# existing column is treated as TEXT-equivalent.
_PG_NUMERIC_FLOAT = frozenset(
    {
        "double precision",
        "real",
        "numeric",
        "decimal",
    },
)
_PG_NUMERIC_INT = frozenset(
    {
        "bigint",
        "integer",
        "smallint",
    },
)
_PG_BOOL = frozenset({"boolean"})


def _validate_identifier(name: str, *, label: str) -> str:
    """Return ``name`` if it is a safe SQL identifier; raise otherwise."""
    if not _IDENTIFIER_PATTERN.fullmatch(name):
        msg = (
            f"{label} must match [A-Za-z_][A-Za-z0-9_]{{0,62}}; got {name!r}. "
            "Schema/table names are interpolated into CREATE/INSERT "
            "statements, so they must be safe identifiers."
        )
        raise ValueError(msg)
    return name


def _column_type(spec: ColumnSpec) -> str:
    """Map a :class:`ColumnSpec` to a PostgreSQL type literal."""
    if spec.python_type is float:
        return "double precision"
    if spec.python_type is bool:
        return "boolean"
    if spec.python_type is int:
        return "bigint"
    return "text"


def _block_summary_row(block: DaqBlock) -> dict[str, float | int | str | bool | None]:
    """Flatten a :class:`DaqBlock` into one summary row.

    Mirrors :func:`nidaqlib.sinks.sqlite._block_summary_row`. The
    block's ``data`` array is intentionally not serialised — use
    ``ParquetSink`` for sample-level archives.
    """
    return {
        "device": block.device,
        "task": block.task,
        "block_index": block.block_index,
        "first_sample_index": block.first_sample_index,
        "samples_per_channel": block.samples_per_channel,
        "block_period_ns": block.block_period_ns,
        "sample_rate_hz": block.sample_rate_hz,
        "task_started_at": block.task_started_at.isoformat(),
        "t0": block.t0.isoformat(),
        "t_mono_ns": block.t_mono_ns,
        "t_utc": block.t_utc.isoformat(),
        "t_midpoint_mono_ns": block.t_midpoint_mono_ns,
        "channels": ",".join(block.channels),
        "units": ",".join(
            unit if unit is not None else ""
            for unit in (block.units.get(channel) for channel in block.channels)
        ),
    }


@dataclass(frozen=True, slots=True)
class PostgresConfig:
    """Connection + target settings for :class:`PostgresSink`.

    Either ``dsn`` or the discrete ``host``/``user``/``database`` set
    must be provided. Credentials are not logged.

    Attributes:
        dsn: Full libpq-style connection string. Mutually exclusive
            with the discrete fields.
        host: Database host. Required if ``dsn`` is not set.
        port: Database port. Defaults to ``5432``.
        user: Database role.
        password: Role password. Never logged.
        database: Database name.
        schema: Target schema. Validated against
            ``[A-Za-z_][A-Za-z0-9_]{0,62}``.
        table_readings: Table name for :class:`DaqReading` rows.
        table_blocks: Table name for :class:`DaqBlock` summary rows.
        pool_min_size: Minimum pool size. Defaults to ``1``.
        pool_max_size: Maximum pool size. Defaults to ``4``.
        statement_timeout_ms: ``statement_timeout`` server setting.
            Defaults to 30 s.
        command_timeout_s: asyncpg's per-call command timeout.
        create_tables: If ``True``, infer per-shape schemas from the
            first batch of each kind and run ``CREATE TABLE IF NOT
            EXISTS``. If ``False`` (default), require the tables to
            exist and lock per-shape schemas from
            ``information_schema.columns``.
        use_copy: If ``True`` (default), bulk-write via asyncpg's
            binary COPY path. Disable to fall back to prepared
            ``executemany``.
    """

    dsn: str | None = None
    host: str | None = None
    port: int = 5432
    user: str | None = None
    password: str | None = None
    database: str | None = None
    schema: str = "public"
    table_readings: str = "readings"
    table_blocks: str = "blocks"
    pool_min_size: int = 1
    pool_max_size: int = 4
    statement_timeout_ms: int = 30_000
    command_timeout_s: float = 10.0
    create_tables: bool = False
    use_copy: bool = True

    def __post_init__(self) -> None:
        if self.dsn is None and self.host is None:
            msg = (
                "PostgresConfig requires either `dsn` or `host` (and related "
                "discrete fields); both were None."
            )
            raise ValueError(msg)
        if self.dsn is not None and self.host is not None:
            msg = (
                "PostgresConfig: `dsn` and `host` are mutually exclusive — "
                "pick one connection style."
            )
            raise ValueError(msg)
        _validate_identifier(self.schema, label="schema name")
        _validate_identifier(self.table_readings, label="table_readings")
        _validate_identifier(self.table_blocks, label="table_blocks")
        if self.pool_min_size < 1 or self.pool_max_size < self.pool_min_size:
            msg = (
                f"PostgresConfig: pool bounds invalid "
                f"(min={self.pool_min_size}, max={self.pool_max_size})."
            )
            raise ValueError(msg)
        if self.statement_timeout_ms < 0:
            raise ValueError(
                f"statement_timeout_ms must be >= 0, got {self.statement_timeout_ms!r}",
            )
        if self.command_timeout_s <= 0:
            raise ValueError(
                f"command_timeout_s must be > 0, got {self.command_timeout_s!r}",
            )

    def target(self) -> str:
        """Return a log-safe URI describing the connection target."""
        if self.dsn is not None:
            parsed = urlparse(self.dsn)
            host = parsed.hostname or "?"
            port = parsed.port or self.port
            db = (parsed.path or "/?").lstrip("/") or "?"
            scheme = parsed.scheme or "postgres"
        else:
            host = self.host or "?"
            port = self.port
            db = self.database or "?"
            scheme = "postgres"
        return urlunparse((scheme, f"{host}:{port}", f"/{db}.{self.schema}", "", "", ""))


def _load_asyncpg() -> Any:
    """Lazy-import asyncpg; raise :class:`NIDaqSinkDependencyError` on miss."""
    try:
        import asyncpg  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]  # noqa: PLC0415
    except ImportError as exc:
        raise NIDaqSinkDependencyError(
            "PostgresSink requires the `postgres` extra. "
            "Install with: `pip install 'nidaqlib[postgres]'` "
            "(or `uv add 'nidaqlib[postgres]'`).",
        ) from exc
    return asyncpg


class _TableState:
    """Per-shape schema lock + cached INSERT SQL."""

    __slots__ = ("insert_sql", "lock", "table")

    def __init__(self, *, table: str, sink_name: str) -> None:
        self.table = table
        self.lock = SchemaLock(sink_name=sink_name, logger=_logger)
        self.insert_sql: str | None = None


class PostgresSink:
    """Append-only Postgres writer for DAQ readings and block summaries.

    One sink instance routes records to up to two tables, one per
    shape (readings / blocks). Each table's column set is
    locked on first write (``create_tables=True``) or read on
    :meth:`open` from ``information_schema.columns``
    (``create_tables=False``, the default).
    """

    def __init__(self, config: PostgresConfig) -> None:
        self._config = config
        self._asyncpg: Any = None
        self._pool: Any = None
        self._readings = _TableState(
            table=config.table_readings,
            sink_name="postgres.readings",
        )
        self._blocks = _TableState(
            table=config.table_blocks,
            sink_name="postgres.blocks",
        )
        self._rows_written = 0

    @property
    def config(self) -> PostgresConfig:
        """The frozen :class:`PostgresConfig` passed in at construction."""
        return self._config

    async def open(self) -> None:
        """Load asyncpg, open the pool, and (optionally) introspect tables.

        Idempotent. When ``create_tables=False`` (the default), each
        target's columns are read on open and the per-shape schemas
        locked immediately. When ``create_tables=True`` the locks
        happen lazily on the first :meth:`write_many` / :meth:`write`
        of each shape.
        """
        if self._pool is not None:
            return
        self._asyncpg = _load_asyncpg()
        cfg = self._config
        server_settings = {
            "application_name": "nidaqlib",
            "statement_timeout": str(int(cfg.statement_timeout_ms)),
        }
        try:
            if cfg.dsn is not None:
                self._pool = await self._asyncpg.create_pool(
                    dsn=cfg.dsn,
                    min_size=cfg.pool_min_size,
                    max_size=cfg.pool_max_size,
                    command_timeout=cfg.command_timeout_s,
                    server_settings=server_settings,
                )
            else:
                self._pool = await self._asyncpg.create_pool(
                    host=cfg.host,
                    port=cfg.port,
                    user=cfg.user,
                    password=cfg.password,
                    database=cfg.database,
                    min_size=cfg.pool_min_size,
                    max_size=cfg.pool_max_size,
                    command_timeout=cfg.command_timeout_s,
                    server_settings=server_settings,
                )
        except Exception as exc:
            raise NIDaqSinkWriteError(
                f"PostgresSink: failed to open pool for {cfg.target()}: {exc}",
            ) from exc

        _logger.info(
            "sinks.postgres.open target=%s pool_min=%s pool_max=%s use_copy=%s create_tables=%s",
            cfg.target(),
            cfg.pool_min_size,
            cfg.pool_max_size,
            cfg.use_copy,
            cfg.create_tables,
        )

        if not cfg.create_tables:
            for state in (self._readings, self._blocks):
                await self._introspect_existing_table(state)

    async def _introspect_existing_table(self, state: _TableState) -> None:
        """Read ``information_schema.columns`` and lock the schema for one table."""
        cfg = self._config
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = $1 AND table_name = $2
                    ORDER BY ordinal_position
                    """,
                    cfg.schema,
                    state.table,
                )
        except Exception as exc:
            raise NIDaqSinkWriteError(
                f"PostgresSink: failed to introspect "
                f"{cfg.schema}.{state.table} on {cfg.target()}: {exc}",
            ) from exc
        if not rows:
            msg = (
                f"PostgresSink: table {cfg.schema}.{state.table} does not exist "
                f"on {cfg.target()} and create_tables=False. Create the table "
                "first, or pass create_tables=True on PostgresConfig."
            )
            raise NIDaqSinkSchemaError(msg)
        specs: list[ColumnSpec] = []
        for row in rows:
            data_type = str(row["data_type"]).lower()
            py_type: type
            if data_type in _PG_NUMERIC_FLOAT:
                py_type = float
            elif data_type in _PG_BOOL:
                py_type = bool
            elif data_type in _PG_NUMERIC_INT:
                py_type = int
            else:
                py_type = str
            specs.append(
                ColumnSpec(
                    name=str(row["column_name"]),
                    python_type=py_type,
                    nullable=True,
                ),
            )
        state.lock.lock_to(specs)
        state.insert_sql = self._build_insert_sql(state)

    async def write_many(self, items: Sequence[DaqReading]) -> None:
        """Append :class:`DaqReading` rows."""
        if self._pool is None:
            raise RuntimeError("PostgresSink: write_many called before open()")
        if not items:
            return
        rows = [reading_to_row(r) for r in items]
        await self._insert_rows(rows, state=self._readings)

    async def write(self, block: DaqBlock) -> None:
        """Append one :class:`DaqBlock` as a summary row."""
        if self._pool is None:
            raise RuntimeError("PostgresSink: write called before open()")
        await self._insert_rows([_block_summary_row(block)], state=self._blocks)

    async def _insert_rows(
        self,
        rows: list[dict[str, float | int | str | bool | None]],
        *,
        state: _TableState,
    ) -> None:
        if not rows:
            return
        if not state.lock.is_locked:
            assert self._config.create_tables  # noqa: S101
            state.lock.lock(rows)
            await self._create_table(state)
            state.insert_sql = self._build_insert_sql(state)

        columns = state.lock.columns
        assert columns is not None  # noqa: S101
        assert state.insert_sql is not None  # noqa: S101

        projected_tuples: list[tuple[object, ...]] = []
        for row in rows:
            fields = state.lock.project(row)
            projected_tuples.append(tuple(fields[spec.name] for spec in columns))

        try:
            if self._config.use_copy:
                await self._write_copy(projected_tuples, columns, state=state)
            else:
                await self._write_executemany(projected_tuples, state=state)
        except NIDaqSinkWriteError:
            raise
        except Exception as exc:
            raise NIDaqSinkWriteError(
                f"PostgresSink: write failed for {self._config.target()}.{state.table}: {exc}",
            ) from exc
        self._rows_written += len(projected_tuples)

    async def _write_copy(
        self,
        records: Sequence[tuple[object, ...]],
        columns: Sequence[ColumnSpec],
        *,
        state: _TableState,
    ) -> None:
        """Bulk-insert via asyncpg's binary COPY path."""
        cfg = self._config
        async with self._pool.acquire() as conn:
            await conn.copy_records_to_table(
                state.table,
                records=list(records),
                columns=[spec.name for spec in columns],
                schema_name=cfg.schema,
                timeout=cfg.command_timeout_s,
            )

    async def _write_executemany(
        self,
        records: Sequence[tuple[object, ...]],
        *,
        state: _TableState,
    ) -> None:
        """Insert via prepared ``executemany`` (COPY-off fallback)."""
        assert state.insert_sql is not None  # noqa: S101
        async with (
            self._pool.acquire() as conn,
            conn.transaction(),
        ):
            await conn.executemany(state.insert_sql, records)

    def _build_insert_sql(self, state: _TableState) -> str:
        """Compose the parameterised INSERT used by the executemany fallback.

        Identifiers (schema, table, column names) are validated or
        library-sourced — never user input. Values go through ``$N``
        placeholders.
        """
        columns = state.lock.columns
        assert columns is not None  # noqa: S101
        col_list = ", ".join(f'"{spec.name}"' for spec in columns)
        placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
        cfg = self._config
        # S608: identifiers validated at config construction; values are $N.
        return (
            f'INSERT INTO "{cfg.schema}"."{state.table}" '  # noqa: S608
            f"({col_list}) VALUES ({placeholders})"
        )

    async def _create_table(self, state: _TableState) -> None:
        """Issue ``CREATE TABLE IF NOT EXISTS`` from the inferred schema."""
        cfg = self._config
        columns = state.lock.columns
        assert columns is not None  # noqa: S101
        col_defs = ", ".join(f'"{spec.name}" {_column_type(spec)}' for spec in columns)
        # Identifiers validated in PostgresConfig.__post_init__.
        stmt = f'CREATE TABLE IF NOT EXISTS "{cfg.schema}"."{state.table}" ({col_defs})'
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(stmt)
        except Exception as exc:
            raise NIDaqSinkWriteError(
                f"PostgresSink: CREATE TABLE failed for {cfg.schema}.{state.table}: {exc}",
            ) from exc

    async def close(self) -> None:
        """Close the pool. Idempotent."""
        if self._pool is None:
            return
        pool = self._pool
        self._pool = None
        try:
            await pool.close()
        finally:
            _logger.info(
                "sinks.postgres.close target=%s rows_written=%s",
                self._config.target(),
                self._rows_written,
            )
        self._asyncpg = None

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
