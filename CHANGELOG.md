# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
  - `DaqManager` and `TaskResult` (design doc §15) — multi-task
    lifecycle with per-task locks, LIFO unwind, idempotent ref-counted
    `add` / `remove`, and `ExceptionGroup` semantics on group failures.
    Honours `ErrorPolicy.RAISE` / `ErrorPolicy.RETURN` for `start`,
    `stop`, `poll`, `read_block`.
  - Discovery-driven preflight in `DaqManager.add` raising
    `NIDaqResourceError` on obvious physical-channel overlap (best
    effort; NI is final authority).
  - `record_polled` accepts `DaqSession | DaqManager`; manager mode
    emits `Mapping[str, TaskResult[DaqReading]]` per tick.
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
