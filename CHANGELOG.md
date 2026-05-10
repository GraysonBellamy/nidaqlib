# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-05-10

### Added

- Per-channel analog-input configuration:
  - `AnalogInputBase` shared parent for `AnalogInputVoltage` and
    `ThermocoupleInput`. Carries the per-channel knobs NI exposes only
    as channel properties on the object returned by
    `add_ai_*_chan(...)` — not as kwargs.
  - `adc_timing_mode` (`ADCTimingMode`) plus `adc_custom_timing_mode`
    for the `ADCTimingMode.CUSTOM` case. Trades conversion rate for
    resolution and configures line-frequency rejection on delta-sigma
    modules (NI 9213 / 9214, 9239, 4300-series). `__post_init__`
    rejects mismatched pairing — `CUSTOM` requires the integer code,
    and the integer code is only valid with `CUSTOM`.
  - `auto_zero_mode` (`AutoZeroType`) — `NONE` / `ONCE` /
    `EVERY_SAMPLE`. Controls per-channel auto-zero calibration on
    modules that support it.
  - `NidaqmxBackend._apply_ai_channel_attrs` writes both attributes as
    channel properties after `add_ai_*_chan` returns. Module-level
    support is detected at set time; unsupported attributes surface as
    `NIDaqBackendError` carrying NI's error code.
- `nidaqlib.constants` module re-exporting NI driver constants:
  `ADCTimingMode`, `AutoZeroType`, `CJCSource`, `LoggingMode`,
  `LoggingOperation`, `TemperatureUnits`, `TerminalConfiguration`,
  `ThermocoupleType`. All eight also re-exported at the top level so
  `from nidaqlib import ADCTimingMode` works.
- `docs/channels.md`: new "AI channel attributes", "ADC timing mode",
  and "Auto-zero mode" sections covering trade-offs, mode tables, and
  hardware-support caveats.
- Synchronization, counters, and metadata:
  - `TriggerSpec` hierarchy (design doc §8.1):
    `DigitalEdgeStartTrigger`, `AnalogEdgeStartTrigger`,
    `DigitalEdgeReferenceTrigger`. `TaskSpec.trigger` now accepts
    `TriggerSpec | None`. JSON `to_dict` / `from_dict` round-trip via a
    registry on the base class. `AnalogTriggerSlope` enum re-exported.
  - `NidaqmxBackend.configure_trigger` (NI ordering: timing first,
    trigger after) and `FakeDaqBackend.configure_trigger` for tests.
  - Counter-input channels: `CounterFrequencyInput`,
    `CounterPeriodInput`, `CounterEdgeCountInput`. Counter-output
    channels with safety metadata: `CounterPulseFrequency`,
    `CounterPulseTime`, `CounterPulseTicks`. Backend dispatch wired in
    `NidaqmxBackend.add_channel`.
  - `DaqManager.start_synchronized(master, slaves, ...)` for multi-task
    coordination — slaves armed sequentially before the master,
    automatic LIFO rollback if any slave fails to arm.
  - `RunMetadata` (design doc §18.2) with auto-detected library /
    driver / interpreter versions, JSON `to_dict` / `from_dict`, and
    sidecar helpers `write_sidecar` / `read_sidecar` /
    `sidecar_path_for` (design doc §18.4).
  - `docs/timing.md` filled in: sample-clock timing, triggers, and the
    `start_synchronized` recipe.
  - Example `examples/synchronized_acquisition.py` covering the
    master / slave pattern end-to-end.
- Outputs and manager:
  - Output / digital channel specs: `AnalogOutputVoltage` (with
    `safe_min` / `safe_max` / `requires_confirm` per design doc §17.3),
    `DigitalInput`, `DigitalOutput`.
  - `DaqSession.write(values, *, confirm=False)` with the §17 safety
    gate — refuses on missing/unknown keys, refuses without
    `confirm=True` whenever any target channel sets `requires_confirm`,
    and rejects (never silently clamps) AO values outside the resolved
    `safe_min` / `safe_max` window.
  - `DaqManager` and `DeviceResult` (design doc §15) — multi-task
    lifecycle with per-task locks, LIFO unwind, idempotent ref-counted
    `add` / `remove`, and `ExceptionGroup` semantics on group failures.
    Honours `ErrorPolicy.RAISE` / `ErrorPolicy.RETURN` for `start`,
    `stop`, `poll`, `read_block`.
  - Discovery-driven preflight in `DaqManager.add` raising
    `NIDaqResourceError` on obvious physical-channel overlap (best
    effort; NI is final authority).
  - `record_polled` accepts `DaqSession | DaqManager`; manager mode
    emits `Mapping[str, DeviceResult[DaqReading]]` per tick.
  - `PostgresSink` / `PostgresConfig` behind the `postgres` extra for
    row-oriented readings/samples plus block summary rows.
  - CLIs: `nidaq-read` (one-shot or streamed scalar), `nidaq-info`
    (driver / device / library report).
  - `docs/safety.md` rewritten with the tiered safety model, gate
    behaviour, and recommended patterns.
  - New error classes: `NIDaqWriteError`, `NIDaqResourceError`.
  - `NidaqmxBackend.write` (AO / DO via stream writers) and
    `add_channel` dispatch for AO / DI / DO. `FakeDaqBackend` mirrors
    write recording (`_FakeTask.writes` log) and scripted
    `write_errors`.
- Repository scaffold: `pyproject.toml`, `pre-commit`, GitHub
  Actions CI / docs / release workflows, `zensical` docs site, `uv` dependency
  groups, `ruff` / `mypy` / `pyright` configuration, `pytest` with AnyIO
  cross-backend fixture, and stub modules per design doc §6.
- `_logging.py` and `_runtime.py` ported from the sibling
  `alicatlib` / `sartoriuslib` packages.
- Design document (`docs/design.md`).

### Changed

- CLIs (`nidaq-read`, `nidaq-capture`) and the test suite now import NI
  driver constants from `nidaqlib.constants` rather than reaching
  through to `nidaqmx.constants` directly. The members are the same
  enum objects — `nidaqlib` does not re-shape them.

### Fixed

- `AnalogInputVoltage.terminal_config` is now serialised via the
  enum's `.value` int and restored on `from_dict`. Prior versions
  stored the enum object as-is, breaking JSON round-trip whenever
  `terminal_config` was set.

[0.2.0]: https://github.com/GraysonBellamy/nidaqlib/releases/tag/v0.2.0
[0.1.0]: https://github.com/GraysonBellamy/nidaqlib/releases/tag/v0.1.0
