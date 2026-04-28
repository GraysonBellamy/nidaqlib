## Summary

<!-- What changes and why. Link to the design section it realises, if any. -->

## Scope

- [ ] Touches a public API surface
- [ ] Touches the backend layer (`NidaqmxBackend` / `FakeDaqBackend`)
- [ ] Adds or changes a `ChannelSpec` subclass or task lifecycle
- [ ] Changes a safety gate or output write path
- [ ] Touches the §11.3.2 callback bridge / streaming recorder

## Test plan

- [ ] `uv run pytest` green locally
- [ ] `uv run ruff check .` clean
- [ ] `uv run mypy` clean (no new ignores)
- [ ] New behaviour has a fake-backend test (no NI driver / hardware required)
- [ ] Hardware-only tests marked (`hardware`, `hardware_stateful`, `hardware_output`, `hardware_destructive`)

## Safety checklist (output / actuator changes only)

- [ ] Analog/digital/counter output writes require `confirm=True` before I/O
- [ ] `safe_min` / `safe_max` validation happens before the backend call
- [ ] No new silent fallbacks on NI driver errors — all wrapped via `ErrorContext`
- [ ] TDMS / file-path inputs validated at the boundary
