---
description: nidaqlib is a typed, async/sync NI-DAQmx acquisition layer for Python — declarative task specs, block-oriented reads, TDMS/Parquet logging, and hardware-free tests.
---

# nidaqlib

Experiment-facing NI-DAQmx acquisition tools for Python.

`nidaqlib` is not a replacement for NI's [`nidaqmx-python`](https://github.com/ni/nidaqmx-python). It is a typed, lifecycle-managed acquisition layer built on top of it, designed to fit the same scientific-instrumentation ecosystem as [`alicatlib`](https://github.com/GraysonBellamy/alicatlib) and [`sartoriuslib`](https://github.com/GraysonBellamy/sartoriuslib).

Use `nidaqlib` when you want:

- declarative task specifications,
- consistent async/sync APIs,
- structured errors,
- block-oriented acquisition,
- TDMS / Parquet / SQLite / Postgres / CSV / JSONL logging,
- hardware-free tests,
- and unified experiment workflows across DAQ, flow controllers, and balances.

See [Design](design.md) for the architectural reference.
