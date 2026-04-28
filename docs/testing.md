# Testing

`FakeDaqBackend` supports hardware-free unit tests. See [design doc §21](design.md).

## Hardware test gating

Tests that need real NI hardware are split into four tiers, all skipped by default in CI:

| Marker | Opt-in env var | Description |
|---|---|---|
| `hardware` | `NIDAQLIB_ENABLE_HARDWARE_TESTS=1` | Read-only access to a connected DAQ device. |
| `hardware_stateful` | `NIDAQLIB_ENABLE_STATEFUL_TESTS=1` | Changes task / device state (start/stop/reconfigure). |
| `hardware_output` | `NIDAQLIB_ENABLE_OUTPUT_TESTS=1` | Writes analog / digital / counter output. |
| `hardware_destructive` | `NIDAQLIB_ENABLE_DESTRUCTIVE_TESTS=1` | Calibration or other potentially unsafe operations. |

Channel selection (when running hardware tests) is configured via:

```text
NIDAQLIB_TEST_AI_CHANNEL=Dev1/ai0
NIDAQLIB_TEST_AO_CHANNEL=Dev1/ao0
NIDAQLIB_TEST_DI_LINE=Dev1/port0/line0
NIDAQLIB_TEST_DO_LINE=Dev1/port0/line1
```
