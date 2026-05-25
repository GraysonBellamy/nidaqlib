---
description: Install nidaqlib with uv or pip, add optional extras for Parquet/SQLite/Postgres sinks, and verify the NI-DAQmx driver for hardware-free or real-device use.
---

# Installation

```bash
uv add nidaqlib
```

Optional extras:

```bash
uv add 'nidaqlib[parquet]'    # ParquetSink
uv add 'nidaqlib[postgres]'   # PostgresSink
```

`nidaqlib` requires the NI-DAQmx **driver runtime** for any real-hardware
operation; the Python layer (`nidaqmx-python`) is pulled in automatically but
the driver is a separate platform-specific install. See the
[NI-DAQmx driver downloads](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html).

Tests do **not** require the driver — `FakeDaqBackend` covers the test surface.
