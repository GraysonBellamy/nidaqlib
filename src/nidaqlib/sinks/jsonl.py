"""JSONL sink — one JSON object per line, no schema lock.

Each row stands alone; a record carrying a wider schema simply emits a
wider object without affecting earlier or later rows. Refuses blocks by
default; ``accept_blocks=True`` opts into per-sample scalarisation via
:func:`block_to_rows` — same guard rails as :class:`CsvSink`.

Stdlib-only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Self

from nidaqlib.errors import NIDaqSinkSchemaError
from nidaqlib.sinks.base import block_to_rows, reading_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from io import TextIOWrapper
    from types import TracebackType

    from nidaqlib.tasks.models import DaqBlock, DaqReading


__all__ = ["JsonlSink"]


class JsonlSink:
    """Append-only JSONL writer — one flattened record per line.

    Args:
        path: Destination file. Created or overwritten on :meth:`open`.
        accept_blocks: When ``True``, :meth:`write` calls
            :func:`block_to_rows`. Default ``False`` raises
            :class:`NIDaqSinkSchemaError`.
    """

    def __init__(self, path: str | Path, *, accept_blocks: bool = False) -> None:
        self._path = Path(path)
        self._accept_blocks = accept_blocks
        self._file: TextIOWrapper | None = None

    @property
    def path(self) -> Path:
        """Destination file path."""
        return self._path

    async def open(self) -> None:
        """Open the JSONL file for writing. Overwrites any existing file."""
        if self._file is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("w", encoding="utf-8", newline="")

    async def write_many(self, items: Sequence[DaqReading]) -> None:
        """Serialise each :class:`DaqReading` as one JSON object per line."""
        if self._file is None:
            raise RuntimeError("JsonlSink: write_many called before open()")
        if not items:
            return
        self._write_rows([reading_to_row(item) for item in items])

    async def write(self, block: DaqBlock) -> None:
        """Refuse blocks unless ``accept_blocks=True``."""
        if not self._accept_blocks:
            raise NIDaqSinkSchemaError(
                "JsonlSink refuses DaqBlock by default — pass accept_blocks=True "
                "to scalarise via block_to_rows."
            )
        if self._file is None:
            raise RuntimeError("JsonlSink: write called before open()")
        self._write_rows(block_to_rows(block))

    def _write_rows(self, rows: list[dict[str, float | int | str | bool | None]]) -> None:
        if not rows:
            return
        assert self._file is not None  # noqa: S101
        for row in rows:
            self._file.write(json.dumps(row, ensure_ascii=False))
            self._file.write("\n")
        self._file.flush()

    async def close(self) -> None:
        """Flush and close the JSONL file. Idempotent."""
        if self._file is None:
            return
        try:
            self._file.flush()
        finally:
            self._file.close()
            self._file = None

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
