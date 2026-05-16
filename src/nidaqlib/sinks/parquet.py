"""Parquet sink — :mod:`pyarrow`, row groups per block, zstd by default.

The preferred sink for hardware-clocked acquisition. One row group per
:meth:`write` call (one block) — a crash mid-run loses at most the
current block.

Shape-locking. The first call to either :meth:`write` or :meth:`write_many`
locks the schema. Mixing record shapes after the first write raises
:class:`NIDaqSinkSchemaError`.

Block layout (long-format) — one row per ``(channel, sample)``, with
``t_mono_ns`` / ``t_utc`` per row reconstructed via
:func:`nidaqlib.block_to_rows`.

pyarrow is an optional dependency behind ``nidaqlib[parquet]``; the import
defers to :meth:`open` so instantiating the sink succeeds on bare-core
installs.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

from nidaqlib._logging import get_logger
from nidaqlib.errors import NIDaqSinkDependencyError, NIDaqSinkSchemaError, NIDaqSinkWriteError
from nidaqlib.sinks._schema import ColumnSpec, SchemaLock
from nidaqlib.sinks.base import block_to_rows, reading_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from nidaqlib.tasks.models import DaqBlock, DaqReading


__all__ = ["ParquetSink"]


_logger = get_logger("sinks.parquet")
_Compression = Literal["zstd", "snappy", "gzip", "brotli", "lz4", "none"]


def _load_pyarrow() -> tuple[Any, Any]:
    """Lazy-import pyarrow; raise :class:`NIDaqSinkDependencyError` on miss."""
    try:
        # PLC0415: deferred so bare-core installs can still import the sink.
        import pyarrow as pa  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]  # noqa: PLC0415
        import pyarrow.parquet as pq  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]  # noqa: PLC0415
    except ImportError as exc:
        raise NIDaqSinkDependencyError(
            "ParquetSink requires the `parquet` extra. "
            "Install with: `uv add 'nidaqlib[parquet]'` "
            "(or `pip install 'nidaqlib[parquet]'`)."
        ) from exc
    return pa, pq


class ParquetSink:
    """Parquet writer with first-write shape lock.

    Args:
        path: Destination Parquet file.
        compression: Codec for every row group. ``zstd`` matches or beats
            snappy on speed with ~30% better ratios.
        use_dictionary: Dictionary encoding for string columns.
        row_group_size: Optional max rows per row group. ``None`` lets
            pyarrow batch the whole call.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        compression: _Compression = "zstd",
        use_dictionary: bool = True,
        row_group_size: int | None = None,
    ) -> None:
        self._path = Path(path)
        self._compression: _Compression = compression
        self._use_dictionary = use_dictionary
        if row_group_size is not None and row_group_size < 1:
            raise ValueError(f"row_group_size must be >= 1 if set, got {row_group_size!r}")
        self._row_group_size = row_group_size
        self._schema = SchemaLock(sink_name="parquet", logger=_logger)
        self._shape: Literal["readings", "blocks"] | None = None
        self._pa: Any = None
        self._pq: Any = None
        self._arrow_schema: Any = None
        self._writer: Any = None
        self._rows_written = 0

    @property
    def path(self) -> Path:
        """Destination Parquet file path."""
        return self._path

    @property
    def compression(self) -> _Compression:
        """Configured compression codec."""
        return self._compression

    @property
    def columns(self) -> tuple[ColumnSpec, ...] | None:
        """Locked columns in order, or ``None`` before first write."""
        return self._schema.columns

    @property
    def shape(self) -> Literal["readings", "blocks"] | None:
        """Locked record shape, or ``None`` before first write."""
        return self._shape

    async def open(self) -> None:
        """Load pyarrow and create the parent directory.

        The :class:`pyarrow.parquet.ParquetWriter` itself is opened lazily
        on the first write — we don't have the schema until then.
        """
        if self._pa is not None:
            return
        self._pa, self._pq = _load_pyarrow()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _logger.info(
            "sinks.parquet.open",
            extra={"path": str(self._path), "compression": self._compression},
        )

    async def write_many(self, items: Sequence[DaqReading]) -> None:
        """Append :class:`DaqReading` rows.

        First call locks the schema and the record shape. Mixing shapes
        afterwards raises :class:`NIDaqSinkSchemaError`.
        """
        if self._pa is None:
            raise RuntimeError("ParquetSink: write_many called before open()")
        if not items:
            return
        self._lock_or_check_shape("readings")
        self._write_rows([reading_to_row(r) for r in items])

    async def write(self, block: DaqBlock) -> None:
        """Append one :class:`DaqBlock` as a row group of long-format rows.

        Long-format layout: one row per (channel, sample). The
        ``block_index`` / ``sample_index`` columns let consumers
        re-aggregate efficiently.
        """
        if self._pa is None:
            raise RuntimeError("ParquetSink: write called before open()")
        self._lock_or_check_shape("blocks")
        self._write_rows(block_to_rows(block))

    def _lock_or_check_shape(self, shape: Literal["readings", "blocks"]) -> None:
        """Lock ``self._shape`` on first write; reject mixed shapes after."""
        if self._shape is None:
            self._shape = shape
            return
        if self._shape != shape:
            raise NIDaqSinkSchemaError(
                f"ParquetSink locked on {self._shape!r}; cannot mix {shape!r} into the same file"
            )

    def _write_rows(self, rows: list[dict[str, float | int | str | bool | None]]) -> None:
        if not rows:
            return
        if not self._schema.is_locked:
            self._schema.lock(rows)
            self._arrow_schema = self._build_arrow_schema()
            self._writer = self._open_writer()

        assert self._writer is not None  # noqa: S101
        assert self._arrow_schema is not None  # noqa: S101
        assert self._pa is not None  # noqa: S101

        projected = [self._schema.project(r) for r in rows]
        columns = self._schema.columns
        assert columns is not None  # noqa: S101

        arrays = {spec.name: [row[spec.name] for row in projected] for spec in columns}
        try:
            table = self._pa.Table.from_pydict(arrays, schema=self._arrow_schema)
            self._writer.write_table(table, row_group_size=self._row_group_size)
        except Exception as exc:
            raise NIDaqSinkWriteError(f"ParquetSink: write failed for {self._path}: {exc}") from exc
        self._rows_written += len(projected)

    def _build_arrow_schema(self) -> Any:
        assert self._pa is not None  # noqa: S101
        columns = self._schema.columns
        assert columns is not None  # noqa: S101
        pa = self._pa
        fields: list[Any] = []
        for spec in columns:
            if spec.python_type is float:
                arrow_type = pa.float64()
            elif spec.python_type is bool:
                arrow_type = pa.bool_()
            elif spec.python_type is int:
                arrow_type = pa.int64()
            else:
                arrow_type = pa.string()
            fields.append(pa.field(spec.name, arrow_type, nullable=True))
        return pa.schema(fields)

    def _open_writer(self) -> Any:
        assert self._pq is not None  # noqa: S101
        return self._pq.ParquetWriter(
            str(self._path),
            self._arrow_schema,
            compression=self._compression,
            use_dictionary=self._use_dictionary,
        )

    async def close(self) -> None:
        """Flush the footer and close the writer. Idempotent."""
        if self._writer is not None:
            try:
                self._writer.close()
            finally:
                self._writer = None
        self._pa = None
        self._pq = None
        _logger.info(
            "sinks.parquet.close",
            extra={"path": str(self._path), "rows_written": self._rows_written},
        )

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
