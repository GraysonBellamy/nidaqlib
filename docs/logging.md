# Sinks and logging

`nidaqlib` has three input shapes (`DaqReading`, `DaqSample`, `DaqBlock`)
and three sink Protocols, one per shape. Two pipe drivers thread streams
into sinks. (Design doc §14.1.)

| Sink           | Reading | Sample | Block | Notes |
|----------------|:-:|:-:|:-:|---|
| `InMemorySink` | ✓ | ✓ | ✓ | Tests, REPL, post-run inspection. |
| `CsvSink`      | ✓ | ✓ | ✗ | Refuses blocks unless `accept_blocks=True`. |
| `JsonlSink`    | ✓ | ✓ | ✗ | Same. |
| `SqliteSink`   | ✓ | ✓ | summary only | One row per block (no scalarisation). Different shapes go to different tables. |
| `ParquetSink`  | ✓ | ✓ | ✓ | Preferred for blocks. Row groups per block. zstd by default. |

## When each sink fits

- **`ParquetSink` for hardware-clocked acquisition.** It's the only
  sink that takes `DaqBlock` natively without scalarising. Each call
  to `write(block)` creates one row group; a crash mid-run loses at
  most the current block.
- **`SqliteSink` for cross-instrument scalar logging.** Pair with
  `record_polled` and the same SQLite file Alicat / Sartorius rows go
  to. Block summary rows (one row per block, no per-sample fan-out)
  let you correlate runs without ballooning the table.
- **`CsvSink` / `JsonlSink` for one-off scalar exports.** Both refuse
  blocks by default — set `accept_blocks=True` only when you really
  want one row per (channel, sample) and have measured the file size.
  At 10 kHz × 8 channels for one minute, that's 4.8 million rows.
- **`InMemorySink` for tests.** It captures all three shapes and lets
  you inspect after the recorder closes.

## Pipe drivers

```python
from nidaqlib.sinks import pipe, pipe_blocks

# Row-oriented — DaqReading or DaqSample sequences.
async with record_polled(session, rate_hz=2.0) as (stream, _summary):
    await pipe(stream, sink, batch_size=100, flush_interval_s=1.0)

# Block-native — one DaqBlock per call.
async with record(session, chunk_size=1000) as (stream, _summary):
    await pipe_blocks(stream, parquet_sink)
```

`pipe` flushes whenever the buffer hits `batch_size` or
`flush_interval_s` elapses. `pipe_blocks` writes immediately — blocks
are already batched on the channel × samples axis.

## Why no `pipe_blocks` batching axis?

A `DaqBlock` is already `(n_channels, n_samples)`. Wrapping each block in
a sequence per call would burn allocations in the hot path. Sinks that
need scalar rows opt in via `block_to_long_rows(block)` — never called
automatically.

## Schema lock

Tabular sinks (`CsvSink`, `SqliteSink`, `ParquetSink`) lock their column
set on the first write. Later batches that introduce new columns get a
one-shot `WARN` log and the new columns are dropped. `ParquetSink`
additionally locks the *record shape* — once you've written
`DaqBlock`s, you can't `write_many` `DaqReading`s to the same file
(use a separate sink instance, or a separate file).

## Worked example: Parquet for blocks, SQLite for slow scalars

```python
async with (
    await open_device(spec) as session,
    ParquetSink("run.parquet") as fast,
    SqliteSink("run.sqlite") as slow,
    record(session, chunk_size=1000) as (block_stream, _bs),
    record_polled(session_slow, rate_hz=1.0) as (read_stream, _rs),
    anyio.create_task_group() as tg,
):
    tg.start_soon(pipe_blocks, block_stream, fast)
    tg.start_soon(pipe, read_stream, slow)
```

The two sinks see independent streams. The same `device` /
`monotonic_ns` join key on both lets you correlate them after the
fact.
