---
description: nidaqlib architecture — backend abstraction over nidaqmx-python, task lifecycle, channel and timing specs, streaming pipeline, sinks, and sync facade.
---

# Architecture

`nidaqlib` is a thin async layer over `nidaqmx-python`. The guiding
principle: **wrap workflow, not capability**. Where NI's API is already
clean (channel-type enums, error codes, terminal configuration) we
re-export it. Where the workflow benefits from structure (lifecycle,
backpressure, schemas, sinks) we add it.

## Module layout

```
nidaqlib/
├── tasks/             # TaskSpec, Timing, TdmsLogging, DaqSession, open_device
├── channels/          # ChannelSpec + concrete subclasses (AI/AO/...)
├── backend/           # DaqBackend Protocol; NidaqmxBackend; FakeDaqBackend
├── streaming/         # record (block) + record_polled (scalar)
├── sinks/             # 3 Protocols + 2 pipe drivers + 6 concrete sinks
├── sync/              # SyncPortal-based sync facade
├── system/            # list_devices(), DeviceInfo
├── cli/               # nidaq-list, nidaq-capture, nidaq-read, nidaq-info
├── errors.py          # NIDaqError hierarchy
└── config.py          # NidaqConfig + config_from_env
```

## The backend seam

There is no transport-level seam in DAQ — no bytes on the wire, no
serial protocol to fake. The substitution point is one layer up at the
`DaqBackend` Protocol:

```text
session ──> DaqBackend ──> nidaqmx-python ──> NI driver
                 │
                 └──> FakeDaqBackend (tests, examples)
```

Tests inject `FakeDaqBackend` and exercise the rest of the stack
(`session`, `streaming`, `sinks`) end-to-end without an NI driver
installed.

## Recorder dispatch

Two recorders live side by side; users pick which based on the timing
model of their task:

```text
TaskSpec.timing is None / ON_DEMAND ──>  record_polled (DaqReading)
TaskSpec.timing.mode in {FINITE, CONTINUOUS}  ──>  record (DaqBlock)
```

The choice is yours, not the library's; we don't auto-dispatch because
the correctness models differ (overflow defaults, polling cadence,
TDMS interaction).

## Worker-thread boundaries

`nidaqmx-python` is synchronous. The async layer crosses a worker
thread for every NI call:

```text
async caller
  └─ await anyio.to_thread.run_sync(backend.read_block, …)
     └─ NI blocking read on a worker thread
```

The boundary lives at the **session** layer. Backends are pure-sync;
`DaqSession` is the place that schedules them through `to_thread`. The
event loop stays responsive while NI blocks.

## The §11.3.2 callback bridge

For latency-sensitive use cases, NI exposes an "every N samples"
buffer-event callback that fires from a driver-managed thread. `nidaqlib`
bridges that thread into the async stream via `queue.SimpleQueue`:

```text
NI driver thread ── on_buffer(n) ──> queue.SimpleQueue
                                          │
                                anyio worker thread
                                          │
                              await tx.send_nowait(block)
                                          │
                                anyio.MemoryObjectStream
                                          │
                                async for block in stream:
```

The shutdown ordering is strict (design doc §11.3.2):

1. Unregister the NI callback.
2. Put a sentinel on the queue.
3. Await the drainer's exit.
4. Stop / close the task.

Get any of these out of order and you get either leaked threads or
NI errors. The recorder's `__aexit__` enforces them.

The bridge ships behind `use_callback_bridge=True`. The default Option A
(blocking read in a worker thread) is correct for almost all use cases
and is much harder to get wrong.

## Escape hatch

When `nidaqlib` doesn't expose a NI feature you need, reach through:

```python
async with await open_device(spec) as session:
    session.raw_task.timing.cfg_dig_edge_start_trig("/Dev1/PFI0")
    # ... use the raw nidaqmx.Task object directly
```

The session still owns the lifecycle; you've just borrowed the handle.
This is the same pattern alicatlib and sartoriuslib expose for `transport`
and `protocol`.

## Why no `transport/`, `protocol/`, `commands/`, `registry/`?

Those folders make sense in alicatlib and sartoriuslib because the
serial-protocol world has byte-level seams. NI does not. The
implementation plan (§17) calls out the temptation to add them anyway
"for symmetry" — and rejects it. The right substitution point is
`DaqBackend`; everything below that is NI's stable C ABI.
