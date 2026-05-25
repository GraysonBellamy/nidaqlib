---
description: Bench playbook for validating nidaqlib against real NI thermocouple modules (NI-9211/9212/9213/9214, USB-9211) with the integration test suite.
---

# Hardware Day — Thermocouple Module Playbook

A bench-runnable playbook for validating `nidaqlib` against real NI hardware,
scoped to what a **thermocouple-only** module
(NI-9211 / 9212 / 9213 / 9214 / USB-9211) can actually drive.

Companion to [`design.md`](design.md). Maps to the integration suite under
[`tests/integration/`](../tests/integration/).

---

## What this day covers (and what it doesn't)

A TC module exercises:

- **Session lifecycle** — `open_device`, `DaqSession.read_block`, the
  `raw_task` escape hatch.
- **Acquisition helpers** — `ThermocoupleInput`, `acquire` (finite),
  `record_polled` (software-timed scalar), `record` (block path,
  including the §11.3.2 callback bridge), all sinks except `Postgres`,
  driver-side TDMS, sync facade, the `nidaq-info` / `nidaq-list` CLIs.
- **Manager fan-out** — `DaqManager` lifecycle, refcount, preflight conflict,
  `ErrorPolicy.RETURN`, `record_polled(manager, ...)` fan-out.
- **Run metadata** — TDMS sidecar metadata round-trip (`RunMetadata` ↔
  `<base>.metadata.json`).

A TC module **cannot** exercise — flag and skip:

| Surface | Reason |
|---|---|
| `AnalogOutputVoltage`, `DigitalInput`, `DigitalOutput`, `confirm=True` write gates | No AO / DIO on a TC card. |
| `CounterFrequencyInput`, `CounterPeriodInput`, `CounterEdgeCountInput`, `CounterPulseFrequency`/`Time`/`Ticks` | No counter on a TC card. |
| `TriggerSpec` (analog edge, digital edge, reference) | TC modules expose no PFI / RTSI. |
| Multi-task shared-clock acquisition (`DaqManager.start_synchronized`) | TC modules use implicit timing — no shared sample clock. |
| High-rate stress on the §11.3.2 callback bridge | TC sample rates cap at ~14 S/s (9211) – ~75 S/s (9213). The unit suite already covers load, GC, and cancellation against the fake backend; today is plumbing-only. |
| ~~`nidaq-read` / `nidaq-capture` CLIs~~ | Now supported via `--thermocouple-type J|K|T|E|N|R|S|B`. D3 keeps the voltage-mode-rejection tripwire; D4 covers the TC mode end-to-end. |

---

## Driver host (Linux)

NI-DAQmx requires the `nipalk` kernel module. The userspace stack — even
for USB and Ethernet devices — won't initialise without it (`nipal.service`
fails at `modprobe -q nipalk`). DKMS builds `nipalk` against the running
kernel, so the **host** kernel has to compile it; there's no all-userspace
or container-only path.

### Officially supported hosts

- Ubuntu 22.04 LTS / 24.04 LTS
- RHEL 9 / 10 (and clones)
- openSUSE Leap 15.6 / 16.0

On any of those, install the bootstrap deb/rpm from
[NI's downloads page](https://www.ni.com/en/support/downloads/drivers/download.ni-linux-device-drivers.html),
`apt-get install ni-daqmx ni-tdms-bin` (or the `dnf` equivalents), and
skip straight to [Pre-flight](#pre-flight-15-min).

`ni-tdms-bin` is **not** a hard dependency of the `ni-daqmx` metapackage.
Without it any task started with TDMS logging fails at `task.start()` with
NI **-201310** ("TDMS support is not installed or is too old") — Block C
and the F1 sidecar round-trip both depend on it. The package is small
(~210 KB) and there's no reason to skip it on a host that can drive
`ni-daqmx`.

### Unsupported hosts (Arch / CachyOS / etc.)

NI doesn't ship for these, and a custom kernel (e.g. CachyOS with
EEVDF / LTO / AutoFDO / Propeller patches) is unlikely to compile NI's
DKMS sources cleanly. Ranked options:

| Option | Effort | Reliability |
|---|---|---|
| Ubuntu 22.04 LTS **VM** with USB passthrough or bridged networking | 30–45 min one-time | ✅ High — exactly what NI tested. Persistent; reusable for future bench days. |
| Bootable **Ubuntu LTS USB** for the bench session | 30 min, zero ongoing | ✅ High — cleanest if this is a one-off. |
| Build NI's DKMS modules against the host kernel | 1–3 h, often more | ⚠️ Low–medium — kernel-version-sensitive, re-breaks on every kernel upgrade. |
| `distrobox` / podman container with NI-DAQmx | 15 min userspace install + ∞ kernel fight | ❌ **Doesn't work.** See below. |

### Why containers don't work

Distrobox / podman containers share the host kernel. Inside the container,
NI's DKMS builds all ~47 modules against the **container's** base-image
kernel (typically `5.15.0-generic` on Ubuntu 22.04), which then refuse to
load on the host's kernel. Even with `--privileged` you'd still need the
modules built against the host's exact kernel headers — at which point
you're doing the host-DKMS path, and the container adds nothing. The
userspace daemons (`nimxssvr`, `nisvcloc`) start fine inside the
container, but `nipal.service` fails at the `modprobe nipalk` invocation and
nothing downstream initialises (`nilsdev` segfaults; `libnidaqmx.so`
loads but can't talk to a device node that was never created).

### Status on this bench

- Ubuntu 22.04 LTS VM (kernel 6.8.0-110-generic) stood up; `ni-daqmx`
  26.3 + `ni-tdms-bin` 26.3 installed; `nipalk` / `nikal` kernel modules
  loaded.
- First full bench day completed against an NI 9214 in a cDAQ-9171
  chassis. Current results: 35/35 hardware tests pass (no xfails after
  follow-up fixes — see Block D and Block E); unit suite 265/265 green.
  Findings rolled into the relevant blocks below and into
  [`design.md`](design.md) §11.3.2 / §15.3.

---

## Pre-flight (15 min)

### Wiring & hardware

1. Identify the module in NI MAX. Note the alias (`cDAQ1Mod1`, `Dev1`,
   `cDAQ9189-…`).
2. Connect at least one TC. Two is much better — the second-channel
   tests in Block A, B, C, E only run when a secondary channel is
   configured.
3. If you have a soldering iron or a hand to grip a TC, do the **hot/cold**
   trick: log the warm channel and an ambient channel in parallel — gives
   you an instant visual confirmation that channel order, units, and
   scale are right.

### Environment

```bash
export NIDAQLIB_ENABLE_HARDWARE_TESTS=1
export NIDAQLIB_TEST_TC_DEVICE=cDAQ1Mod1                # ← your alias
export NIDAQLIB_TEST_TC_CHANNEL_PRIMARY=cDAQ1Mod1/ai0
export NIDAQLIB_TEST_TC_CHANNEL_SECONDARY=cDAQ1Mod1/ai1  # optional
export NIDAQLIB_TEST_TC_TYPE=K                           # default K
export NIDAQLIB_TEST_TC_RATE_HZ=10                       # default 10
export NIDAQLIB_TEST_TC_MIN_DEGC=-50                     # default -50
export NIDAQLIB_TEST_TC_MAX_DEGC=200                     # default 200
```

If `NIDAQLIB_TEST_TC_CHANNEL_PRIMARY` is unset the conftest synthesises
`f"{device}/ai0"`. Fixtures and helpers live in
[`tests/integration/conftest.py`](../tests/integration/conftest.py).

### Sanity check (no test code yet)

```bash
uv run nidaq-info --json
uv run nidaq-list
uv run nidaq-list "$NIDAQLIB_TEST_TC_DEVICE" --json
```

Expected:

- `nidaq-info` reports a non-`null` `ni_driver_version` and lists the
  TC device.
- `nidaq-list` shows AI physical channels under the device alias.
- `nidaq-list <device> --json`'s `ai_channels` array contains at least
  `$NIDAQLIB_TEST_TC_CHANNEL_PRIMARY`.

If any of those fails, **stop**. The bench is not in a state where
running the integration suite would tell you anything useful — you'd
just be debugging the connection. Open NI MAX, confirm the device is
present, and re-run.

---

## Block A — session lifecycle (~30 min)

[`tests/integration/test_a_session_lifecycle.py`](../tests/integration/test_a_session_lifecycle.py)

| ID | Test | What it proves |
|---|---|---|
| A1 | `test_a1_poll_returns_reading` | `open_device` + `poll()` round-trip on a TC. Provenance fields (`latency_s`, `monotonic_ns`, `midpoint_at`) populated and consistent. |
| A2 | `test_a2_acquire_finite_block` | FINITE-mode `acquire(N)` returns one `(1, N)` `DaqBlock`; task is auto-stopped after the read. |
| A3 | `test_a3_continuous_read_block_advances_counters` | Five sequential `read_block` calls produce monotonic `block_index` and `first_sample_index`; `task_started_at` anchor stable across the run. |
| A4 | `test_a4_raw_task_escape_hatch` | `session.raw_task` returns the underlying `nidaqmx.Task` immediately after `start`; channel count matches the spec. |
| A5 | `test_a5_two_channel_poll` | Two-channel TC task; one `read_block` returns `(2, N)` data with the right channel order. |
| A6 | `test_a6_poll_rejected_for_continuous_task` | The §9.2 lifecycle guard fires before NI sees the request — `poll()` on a started CONTINUOUS task raises `NIDaqTaskStateError`. |
| A7 | `test_a7_stop_then_restart_same_session` | `stop()` + `start()` on the same session resumes acquisition; second `task_started_at` ≥ first; `read_block` works after the restart. |
| A8 | `test_a8_invalid_sample_rate_rejected` | A 100 kHz request on a 9214 (max ~75 S/s) surfaces a typed `NIDaqError` with NI's code populated on `context.ni_error_code` — confirms the configuration error path is wired correctly through the wrapper. |

```bash
uv run pytest tests/integration/test_a_session_lifecycle.py -v
```

**Expected runtime** ≈ 1 – 2 min on a 9213 at 10 Hz.

---

## Block B — recorders + sinks (~45 min)

[`tests/integration/test_b_recorders_and_sinks.py`](../tests/integration/test_b_recorders_and_sinks.py)

| ID | Test | What it proves |
|---|---|---|
| B1 | `test_b1_record_polled_in_memory` | `record_polled` at 2 Hz for 3 s → ~6 readings into `InMemorySink`. Monotonic timestamps. |
| B2 (×3) | `test_b2_record_polled_to_{sqlite,parquet,jsonl}` | Same shape as B1 into each row-oriented sink; round-trip via `sqlite3.connect` / `pyarrow.parquet.read_table` / line-by-line `json.loads`. |
| B3 | `test_b3_record_blocks_to_parquet` | `record(chunk_size=…)` for 4 blocks. `block_index`/`first_sample_index` strictly increase by `chunk_size`; Parquet has one row group per block. |
| B4 | `test_b4_polled_overflow_drop_oldest` | Producer ≫ consumer with `buffer_size=1` and `OverflowPolicy.DROP_OLDEST` → `summary.blocks_dropped > 0`. |
| B5 | `test_b5_record_with_callback_bridge` | `use_callback_bridge=True` (the §11.3.2 path). Plumbing-only at TC rates. **Caller must use `open_device(spec, autostart=False)`** so the recorder can register the buffer event before NI starts the task — see [§11.3.2](design.md#1132-hardware-timed-high-rate). |
| B6 | `test_b6_csv_sink_refuses_blocks_by_default` | `CsvSink(accept_blocks=False)` raises `NIDaqSinkSchemaError` on the first `write(block)` — §14.1's default-refusal. |
| B7 | `test_b7_csv_sink_accept_blocks_scalarizes` | `CsvSink(accept_blocks=True)` writes one row per `(channel, sample)`. |
| B8 | `test_b8_long_run_polled_drift` | 10 s `record_polled` at 5 Hz into SQLite; reading count within ±5 % of expected, monotonic timestamps strictly increasing, no errors / drops, SQLite row count matches. Catches accumulator / GC-stall bugs that escape the 2–3 s tests. |
| B9 | `test_b9_bridge_cancel_mid_stream_clean_unwind` | Cancelling a bridge-mode `record` mid-stream completes within 5 s — confirms the §11.3.2 stop → unregister → sentinel → drain shutdown does not deadlock or raise on real NI. |
| B10 | `test_b10_bridge_two_channel_blocks` | The bridge correctly reshapes a 2-channel buffer into `(2, chunk_size)` blocks. Two-channel coverage on real hardware confirms the per-channel layout in `_build_block_from_array` is right. |

```bash
uv run pytest tests/integration/test_b_recorders_and_sinks.py -v
```

**Expected runtime** ≈ 3 – 4 min (most tests are bounded by 2 – 3 s of
acquisition).

If `pyarrow` is not installed, the Parquet tests skip cleanly (the suite
uses `pytest.importorskip`). Install it once before the bench day:

```bash
uv sync --extra parquet
```

---

## Block C — TDMS driver-side (~20 min)

[`tests/integration/test_c_tdms_driver_side.py`](../tests/integration/test_c_tdms_driver_side.py)

Requires `nptdms` for file inspection — `uv add --dev nptdms` if it isn't
already there (the tests `importorskip` it).

| ID | Test | What it proves |
|---|---|---|
| C1 | `test_c1_tdms_log_only_emits_empty_stream` | `LoggingMode.LOG` short-circuits the recorder (`blocks_emitted == 0`) and **does not block** waiting for samples NI consumed. TDMS file written and contains samples. Wrapped in `anyio.fail_after(1.0)` — a regression here surfaces as a test failure, not a hung suite. |
| C2 | `test_c2_tdms_log_and_read_dual_path` | `LoggingMode.LOG_AND_READ` populates the TDMS file *and* delivers blocks to the recorder. |

```bash
uv run pytest tests/integration/test_c_tdms_driver_side.py -v
```

**Watch for**: a test that hangs for >5 s on C1 means the §14.6
short-circuit detection has regressed. Cancel and check
`nidaqlib/streaming/block.py:_is_log_only`.

---

## Block D — sync facade + CLI (~15 min)

[`tests/integration/test_d_sync_and_cli.py`](../tests/integration/test_d_sync_and_cli.py)

| ID | Test | What it proves |
|---|---|---|
| D1 | `test_d1_sync_facade_poll` | `Daq.open_device(spec).poll()` from a sync function dispatched through `anyio.to_thread.run_sync`. Returns a sane temperature. |
| D2 (×3) | `test_d2_nidaq_{info_json_lists_device, list_human_lists_device, list_device_json_lists_ai_channels}` | The two CLI tools that *do* work on TC modules report the configured device + AI channels. Subprocess-invoked via `python -m nidaqlib.cli.{info,list}` so we don't depend on installed-script wrappers. |
| D3 | `test_d3_nidaq_read_voltage_mode_rejected_on_tc_module` | **Tripwire.** `nidaq-read` defaults to voltage AI; a TC-only module rejects it with a typed NI error and the CLI exits non-zero. If this ever starts passing without `--thermocouple-type`, the operator's module changed. |
| D4 | `test_d4_nidaq_read_thermocouple_mode` | `nidaq-read --thermocouple-type K cDAQ1Mod1/ai1 --json` returns a sane temperature in `degC`. End-to-end validation of the CLI's TC mode (added after the bench day). |

```bash
uv run pytest tests/integration/test_d_sync_and_cli.py -v
```

**Done**: `--thermocouple-type J|K|T|E|N|R|S|B` is now wired into both
`nidaq-read` and `nidaq-capture`. When set, the CLI builds a
`ThermocoupleInput` channel (defaults `--min`/`--max` to `-50` / `200`
degC) instead of `AnalogInputVoltage`. D4 covers the success path; D3
is a tripwire that the voltage-mode rejection still happens.

---

## Block E — DaqManager (~30 min)

[`tests/integration/test_e_manager.py`](../tests/integration/test_e_manager.py)

| ID | Test | What it proves |
|---|---|---|
| E1 | `test_e1_manager_single_task_read_block` | `DaqManager` with one two-channel TC task end-to-end. `read_block` fan-out emits one `DeviceResult[DaqBlock]`. |
| E2 | `test_e2_refcount_holds_session_alive` | Duplicate `add` bumps refcount; one `remove` does not tear down; the second `remove` does. |
| E3 | `test_e3_preflight_rejects_overlapping_channel` | Adding a second task that targets the same physical channel raises `NIDaqResourceError` (§15.3 best-effort preflight). |
| E4 | `test_e4_invalid_spec_returns_deviceresult_error` | Under `ErrorPolicy.RETURN`, a bogus device alias surfaces as `DeviceResult.error` rather than raising; valid tasks remain operable. |
| E5 | `test_e5_module_reservation_preflight_on_tc_module` | Adding two tasks targeting the same TC module fails at `add()` time with `NIDaqResourceError` referencing module-level reservation. The manager queries `backend.device_info(...)` on first add, caches the product type, and rejects subsequent adds against any whole-module-reserved device (NI 9211/9212/9213/9214). Skips on hardware not in the known reservation set. |

```bash
uv run pytest tests/integration/test_e_manager.py -v
```

**Background**: NI 9211/9212/9213/9214 reserve the **whole module** per
task — two concurrent tasks targeting different AI channels on the same
module are rejected at NI's `task.start()` with **NI -50103** "The
specified resource is reserved." Originally observed on this bench
(NI 9214, cDAQ-9171, 2026 Q2 driver) as a dynamic xfail. The manager
now catches the conflict at `add()` time via `backend.device_info(...)`
+ a hardcoded module-reservation lookup, so the operator never reaches
the NI -50103 path. Documented in
[`design.md` §15.3](design.md#153-resource-model).

---

## Block F — sidecar metadata (~15 min)

[`tests/integration/test_f_sidecar_metadata.py`](../tests/integration/test_f_sidecar_metadata.py)

The sidecar-metadata surface a TC module can drive.

| ID | Test | What it proves |
|---|---|---|
| F1 | `test_f1_sidecar_round_trip` | TDMS run + `RunMetadata` sidecar; `write_sidecar` → `read_sidecar` round-trips through the JSON encoder, including the `ThermocoupleInput` channel kind. |
| F2 | `test_f2_sidecar_path_naming` | `sidecar_path_for(run.tdms)` → `run.metadata.json`; non-`.tdms` extensions get `.metadata.json` appended. |

```bash
uv run pytest tests/integration/test_f_sidecar_metadata.py -v
```

---

## Running the whole suite

```bash
uv run pytest tests/integration/ -v
```

Use `-k` to slice:

```bash
uv run pytest tests/integration/ -k a_       # session lifecycle only
uv run pytest tests/integration/ -k 'b_ or c_'  # streaming + TDMS
```

Without `NIDAQLIB_ENABLE_HARDWARE_TESTS=1` set, every test under
`tests/integration/` is skipped with a clear reason, so you can leave the
suite enabled in your `pytest` configuration without slowing the unit
suite down.

---

## After the bench day

### Capture what you learned

Whatever the suite surfaces — NI error codes from E5, surprising rate
caps, cold-junction quirks — add to a `docs/troubleshooting.md` entry or
the manager's `§15.3` notes. The integration tests are the only
codified knowledge of *which* NI error codes you've actually seen on
real hardware, so they should grow over time, not stay frozen at the
v0.1 set.

### Suggested follow-up tickets

- **CLI ↔ TC**: ✅ done. `--thermocouple-type J|K|T|E|N|R|S|B` is wired
  into both `nidaq-read` and `nidaq-capture`. Covered by D4.
- ✅ **Two-task TC manager**: done. Manager preflight now rejects at
  `add()` with a typed `NIDaqResourceError` for products in the
  module-reservation set (`NI 9211/9212/9213/9214`). E5 covers it on
  real hardware. NI -50103 details and the reservation lookup live in
  [`design.md` §15.3](design.md).
- **Higher-rate hardware coverage**: the §11.3.2 callback bridge (B5) is
  smoke-tested only at TC rates. When a higher-rate module (USB-6001,
  PCIe-6353-class) becomes available, add a Block-B-equivalent that
  drives `chunk_size=1000` at ≥ 1 kHz to actually stress the bridge on
  real hardware.

### Things deliberately left for a different bench day

These are blocked on hardware, not on the library:

- All outputs (`AnalogOutputVoltage`, `DigitalInput`,
  `DigitalOutput`, the `confirm=True` write gates, the
  `safe_min`/`safe_max` clamps).
- All counters (`Counter*Input`, `CounterPulse*`).
- All triggers (`AnalogEdgeStartTrigger`,
  `DigitalEdgeStartTrigger`, `DigitalEdgeReferenceTrigger`).
- Multi-task shared-clock acquisition
  (`DaqManager.start_synchronized` with master/slave).

When the hardware to drive any of these arrives, mirror the
`tests/integration/test_<letter>_<topic>.py` pattern, add fixtures to
`conftest.py`, gate them on the same `NIDAQLIB_ENABLE_HARDWARE_TESTS`
env var (plus the relevant `_OUTPUT_TESTS` / `_DESTRUCTIVE_TESTS` gate
where applicable), and update this document.
