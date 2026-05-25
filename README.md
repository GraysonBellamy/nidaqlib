# nidaqlib

[![CI](https://github.com/GraysonBellamy/nidaqlib/actions/workflows/ci.yml/badge.svg)](https://github.com/GraysonBellamy/nidaqlib/actions/workflows/ci.yml)
[![Docs](https://github.com/GraysonBellamy/nidaqlib/actions/workflows/docs.yml/badge.svg)](https://graysonbellamy.github.io/nidaqlib/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Experiment-facing NI-DAQmx acquisition tools for Python.

`nidaqlib` is **not** a replacement for NI's [`nidaqmx-python`](https://github.com/ni/nidaqmx-python). It is a typed, lifecycle-managed acquisition layer built on top of it, designed to fit the same scientific-instrumentation ecosystem as [`alicatlib`](https://github.com/GraysonBellamy/alicatlib) and [`sartoriuslib`](https://github.com/GraysonBellamy/sartoriuslib).

Use `nidaqlib` when you want:

- declarative task specifications,
- consistent async/sync APIs,
- structured errors,
- block-oriented acquisition,
- TDMS / Parquet / SQLite / Postgres / CSV / JSONL logging,
- hardware-free tests,
- and unified experiment workflows across DAQ, flow controllers, and balances.

## Status

`nidaqlib` is alpha software. The public API is usable, but may change before
the first stable release. See [`docs/design.md`](docs/design.md) for the
architectural design.

## Installation

```bash
uv add nidaqlib
```

Optional extras: `nidaqlib[parquet]`, `nidaqlib[postgres]`.

`nidaqlib` requires the platform NI-DAQmx **driver runtime** for any
real-hardware operation. Tests do not — `FakeDaqBackend` covers the test surface.

## Quickstart

```python
import anyio

from nidaqlib import AnalogInputVoltage, TaskSpec, open_device


spec = TaskSpec(
    name="surface_temperatures",
    channels=[
        AnalogInputVoltage(
            physical_channel="Dev1/ai0",
            name="surface_tc_mv",
            min_val=-0.1,
            max_val=0.1,
        ),
        AnalogInputVoltage(
            physical_channel="Dev1/ai1",
            name="back_tc_mv",
            min_val=-0.1,
            max_val=0.1,
        ),
    ],
)


async def main() -> None:
    async with await open_device(spec) as session:
        reading = await session.poll()
        print(reading.values)


anyio.run(main)
```

## Documentation

Full docs at <https://graysonbellamy.github.io/nidaqlib/>.

## License

MIT — see [LICENSE](LICENSE).
