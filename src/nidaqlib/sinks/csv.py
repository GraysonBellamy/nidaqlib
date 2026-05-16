"""CSV sink — one row per :class:`DaqReading`.

The column order is locked the first time :meth:`write_many` is called.
Unknown columns in later batches are dropped with a one-shot WARN.

Refuses :class:`DaqBlock` by default. Set ``accept_blocks=True`` to opt
into per-sample scalarisation via :func:`block_to_rows` — guards
against accidentally writing 1-GB CSVs at 10 kHz × 8 channels.

Stdlib-only (uses :mod:`csv`).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, Self

from nidaqlib._logging import get_logger
from nidaqlib.errors import NIDaqSinkSchemaError
from nidaqlib.sinks.base import block_to_rows, reading_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from io import TextIOWrapper
    from types import TracebackType

    from nidaqlib.tasks.models import DaqBlock, DaqReading


__all__ = ["CsvSink"]


_logger = get_logger("sinks.csv")


class CsvSink:
    """Append-only CSV writer with first-batch schema lock.

    Args:
        path: Destination file. Created or overwritten on :meth:`open`.
        accept_blocks: When ``True``, :meth:`write` calls
            :func:`block_to_rows` and emits one CSV row per (channel,
            sample). Default ``False`` raises
            :class:`NIDaqSinkSchemaError`.
    """

    def __init__(self, path: str | Path, *, accept_blocks: bool = False) -> None:
        self._path = Path(path)
        self._accept_blocks = accept_blocks
        self._file: TextIOWrapper | None = None
        self._writer: csv.DictWriter[str] | None = None
        self._columns: tuple[str, ...] | None = None
        self._unknown_warned: set[str] = set()

    @property
    def path(self) -> Path:
        """Destination file path."""
        return self._path

    @property
    def columns(self) -> tuple[str, ...] | None:
        """Locked column order, or ``None`` before the first flush."""
        return self._columns

    async def open(self) -> None:
        """Open the CSV file for writing. Overwrites any existing file."""
        if self._file is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("w", encoding="utf-8", newline="")

    async def write_many(self, items: Sequence[DaqReading]) -> None:
        """Append :class:`DaqReading` rows."""
        if self._file is None:
            raise RuntimeError("CsvSink: write_many called before open()")
        if not items:
            return
        rows = [reading_to_row(item) for item in items]
        self._write_rows(rows)

    async def write(self, block: DaqBlock) -> None:
        """Refuse blocks unless ``accept_blocks=True`` was set on construction.

        With ``accept_blocks=True``, per-(channel, sample) rows are emitted
        via :func:`block_to_rows`. The cost of this opt-in is up to
        ``n_channels * samples_per_channel`` rows per block.
        """
        if not self._accept_blocks:
            raise NIDaqSinkSchemaError(
                "CsvSink refuses DaqBlock by default — pass accept_blocks=True "
                "if you really want one CSV row per (channel, sample). At high "
                "rates this can produce gigabyte CSVs; consider ParquetSink."
            )
        if self._file is None:
            raise RuntimeError("CsvSink: write called before open()")
        self._write_rows(block_to_rows(block))

    def _write_rows(self, rows: list[dict[str, float | int | str | bool | None]]) -> None:
        """Append ``rows`` after first-batch schema-lock bookkeeping."""
        if not rows:
            return
        if self._writer is None:
            self._columns = tuple(rows[0].keys())
            self._writer = csv.DictWriter(self._file, fieldnames=list(self._columns))  # type: ignore[arg-type]
            self._writer.writeheader()

        columns = self._columns
        assert columns is not None  # noqa: S101
        column_set = set(columns)

        for row in rows:
            unknown = row.keys() - column_set
            for key in unknown:
                if key not in self._unknown_warned:
                    self._unknown_warned.add(key)
                    _logger.warning(
                        "sinks.csv.unknown_column",
                        extra={"path": str(self._path), "column": key, "action": "drop"},
                    )
            filtered = {k: row.get(k) for k in columns}
            self._writer.writerow(filtered)
        assert self._file is not None  # noqa: S101
        self._file.flush()

    async def close(self) -> None:
        """Flush and close the CSV file. Idempotent."""
        if self._file is None:
            return
        try:
            self._file.flush()
        finally:
            self._file.close()
            self._file = None
            self._writer = None

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
