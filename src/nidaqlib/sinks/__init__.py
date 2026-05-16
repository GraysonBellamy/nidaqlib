"""Data sinks for ``DaqReading`` / ``DaqBlock`` outputs.

Two Protocols (one per record shape) and two pipe drivers. The row-oriented
sinks (CSV, JSONL) refuse :class:`DaqBlock` by default; pass
``accept_blocks=True`` to opt into per-sample scalarisation via
:func:`block_to_rows`.
"""

from __future__ import annotations

from nidaqlib.sinks.base import (
    BlockSink,
    ReadingSink,
    block_to_rows,
    pipe,
    pipe_blocks,
    reading_to_row,
)
from nidaqlib.sinks.csv import CsvSink
from nidaqlib.sinks.jsonl import JsonlSink
from nidaqlib.sinks.memory import InMemorySink
from nidaqlib.sinks.parquet import ParquetSink
from nidaqlib.sinks.postgres import PostgresConfig, PostgresSink
from nidaqlib.sinks.sqlite import SqliteSink

__all__ = [
    "BlockSink",
    "CsvSink",
    "InMemorySink",
    "JsonlSink",
    "ParquetSink",
    "PostgresConfig",
    "PostgresSink",
    "ReadingSink",
    "SqliteSink",
    "block_to_rows",
    "pipe",
    "pipe_blocks",
    "reading_to_row",
]
