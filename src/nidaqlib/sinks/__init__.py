"""Data sinks for ``DaqReading`` / ``DaqSample`` / ``DaqBlock`` outputs.

Three Protocols (one per record shape) and two pipe drivers — design
doc §14.1. The row-oriented sinks (CSV, JSONL) refuse :class:`DaqBlock`
by default; pass ``accept_blocks=True`` to opt into per-sample
scalarisation via :func:`block_to_long_rows`.
"""

from __future__ import annotations

from nidaqlib.sinks.base import (
    BlockSink,
    ReadingSink,
    SampleSink,
    block_to_long_rows,
    pipe,
    pipe_blocks,
    reading_to_row,
    sample_to_row,
)
from nidaqlib.sinks.csv import CsvSink
from nidaqlib.sinks.jsonl import JsonlSink
from nidaqlib.sinks.memory import InMemorySink
from nidaqlib.sinks.parquet import ParquetSink
from nidaqlib.sinks.sqlite import SqliteSink

__all__ = [
    "BlockSink",
    "CsvSink",
    "InMemorySink",
    "JsonlSink",
    "ParquetSink",
    "ReadingSink",
    "SampleSink",
    "SqliteSink",
    "block_to_long_rows",
    "pipe",
    "pipe_blocks",
    "reading_to_row",
    "sample_to_row",
]
