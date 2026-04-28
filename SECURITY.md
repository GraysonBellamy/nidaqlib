# Security policy

## Reporting a vulnerability

Please email [gbellamy@umd.edu](mailto:gbellamy@umd.edu) or open a private
security advisory on GitHub:
<https://github.com/GraysonBellamy/nidaqlib/security/advisories/new>.

Do **not** file public issues for security reports.

## Scope

`nidaqlib` drives NI-DAQmx hardware, including analog/digital/counter outputs
that may be wired to actuators (heaters, valves, igniters, relays). Please
report:

- Code paths that write to analog, digital, or counter outputs without
  enforcing `confirm=True` at the session boundary.
- `safe_min` / `safe_max` validation that can be bypassed (e.g., via a
  direct backend call that the session would otherwise have caught).
- Calibration / device-reset paths that run as a side effect of routine
  operations rather than via an explicit destructive entry point.
- Any path that logs credentials, DSNs, or secrets (`PostgresConfig.password`
  in particular is a non-logging field).
- SQL-injection surfaces in `PostgresSink`.
- Path-traversal or arbitrary-file-write surfaces in `TdmsLogging` / file-based
  sinks (`CsvSink`, `JsonlSink`, `ParquetSink`, `SqliteSink`).
- Deserialisation of untrusted input in fixture loaders or `FakeDaqBackend`
  scripts.
