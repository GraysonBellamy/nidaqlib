# Streaming

`nidaqlib` ships **two recorders, one per acquisition model**. They
have different correctness models — don't try to unify them.

| Recorder         | Path                | Emits           | Default overflow | Use when |
|------------------|---------------------|-----------------|------------------|----------|
| `record`         | hardware-clocked    | `DaqBlock`      | `DROP_OLDEST`    | The NI sample clock owns timing. Rates from a few hundred Hz upward. |
| `record_polled`  | software-timed      | `DaqReading`    | `BLOCK`          | Cross-instrument scalar correlation. Rates ≤ 10 Hz. |

## `record(session, *, chunk_size, ...)`

Hardware-clocked block acquisition. The producer reads `chunk_size`
samples per channel, wraps them as a `DaqBlock`, and pushes onto an
`anyio` memory-object stream. The consumer drains at its own pace.

```python
async with await open_device(spec) as session:
    async with record(session, chunk_size=1000) as (stream, summary):
        async for block in stream:
            ...
```

Two producer paths share the surface:

- **Option A (default)** — blocking read in a worker thread.
- **Option B** — every-N-samples buffer-event callback (the §11.3.2
  driver-thread bridge). Lower latency but harder to get right; opt
  in via `use_callback_bridge=True` once you've measured a need.

### Overflow policies

The NI sample clock cannot pause to wait for a slow consumer — block
the producer too long and the on-board buffer overruns. Hence:

- `DROP_OLDEST` (default) — evict the oldest queued block when the
  outbound stream is full. Keeps the freshest data.
- `DROP_NEWEST` — drop the about-to-be-enqueued block. Bounds consumer
  latency; loses freshest data.
- `BLOCK` — pause the producer. Risks NI buffer overrun; use only when
  you've measured your consumer throughput.

Drops are reported on `summary.blocks_dropped`. Silent loss is never the
answer; for high-rate durable logging, configure TDMS in addition to
the streaming sink — TDMS writes happen on the driver side and are not
subject to consumer back-pressure. See [`tdms.md`](tdms.md).

## `record_polled(session, *, rate_hz, ...)`

Software-timed scalar polling at an absolute target rate. Direct port
of alicatlib's recorder loop.

```python
async with await open_device(spec) as session:
    async with record_polled(session, rate_hz=2.0) as (stream, summary):
        async for reading in stream:
            ...
```

The polled path:

- Uses `anyio.sleep_until` against absolute targets — drift across
  cycles is bounded by one tick and does not accumulate.
- Skips slots when overrunning by more than one period (logs the gap on
  `summary.blocks_dropped`).
- Defaults to `OverflowPolicy.BLOCK` because the software-timed path
  can pause without leaking into NI buffer overrun.

`record_polled` requires `timing=None` or `Timing.mode == ON_DEMAND`
on the session. `ON_DEMAND` is a software-polled marker; it does not
configure an NI sample clock. Hardware-clocked sessions must use `record` —
`session.poll()` (which `record_polled` calls per tick) explicitly
rejects buffered tasks (design doc §9.2).

## `ErrorPolicy`

Both recorders accept `error_policy=`:

- `RAISE` (default) — wrap the NI error, cancel the task group,
  re-raise. Surfaces as a `BaseExceptionGroup` carrying the wrapped
  `NIDaqReadError`.
- `RETURN` — emit a record with `.error` set, advance counters, keep
  going. Consumers MUST gate on `error is None` before reading the
  payload.

Use `RETURN` for long-running unattended captures where one transient
read failure shouldn't kill the whole run.

## `AcquisitionSummary`

A live, mutable counter object yielded alongside each stream. Read it
during the run for progress UI; read it after exit for final counts.

```python
async with record(session, chunk_size=1000) as (stream, summary):
    ...
print(f"emitted={summary.blocks_emitted} dropped={summary.blocks_dropped} "
      f"errors={summary.errors_observed}")
```

The recorder is the only writer; treat the object as read-only on the
consumer side.
