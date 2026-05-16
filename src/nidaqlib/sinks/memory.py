"""In-memory sink — collects records in lists for tests and notebooks.

:class:`InMemorySink` satisfies both sink Protocols
(:class:`~nidaqlib.sinks.base.ReadingSink`,
:class:`~nidaqlib.sinks.base.BlockSink`). Useful for unit tests, REPL
exploration, and short-run captures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from nidaqlib.tasks.models import DaqBlock, DaqReading


__all__ = ["InMemorySink"]


class InMemorySink:
    """Collect every written record in a per-shape list.

    :attr:`readings` / :attr:`blocks` are appended to (never re-assigned).
    :meth:`close` does not clear the buffers — the point of this sink is
    post-run inspection.
    """

    def __init__(self) -> None:
        self._readings: list[DaqReading] = []
        self._blocks: list[DaqBlock] = []
        self._open = False
        self._closed = False

    @property
    def readings(self) -> list[DaqReading]:
        """Captured :class:`DaqReading` records, in write order."""
        return self._readings

    @property
    def blocks(self) -> list[DaqBlock]:
        """Captured :class:`DaqBlock` records, in write order."""
        return self._blocks

    @property
    def is_open(self) -> bool:
        """``True`` once :meth:`open` has been called and ``close`` has not."""
        return self._open and not self._closed

    async def open(self) -> None:
        """No backing resource — flips the open flag."""
        self._open = True
        self._closed = False

    async def write_many(self, items: Sequence[DaqReading]) -> None:
        """Append every :class:`DaqReading` to the readings buffer."""
        if not self.is_open:
            raise RuntimeError("InMemorySink: write_many called before open()")
        if not items:
            return
        self._readings.extend(items)

    async def write(self, block: DaqBlock) -> None:
        """Append one :class:`DaqBlock` to the block buffer."""
        if not self.is_open:
            raise RuntimeError("InMemorySink: write called before open()")
        self._blocks.append(block)

    async def close(self) -> None:
        """Flip the closed flag — buffers preserved for inspection."""
        self._closed = True

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
