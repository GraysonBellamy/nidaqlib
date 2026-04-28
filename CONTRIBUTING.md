# Contributing to nidaqlib

Thanks for your interest. Please read [`docs/design.md`](docs/design.md) before
making non-trivial changes — most design decisions are already made and
documented there.

## Dev setup

```bash
git clone https://github.com/GraysonBellamy/nidaqlib
cd nidaqlib
uv sync --all-extras --dev
uv run pre-commit install
```

## Core checks (must pass before merging)

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pyright
uv run pytest
```

## Adding a new channel type

Per design doc §8 and §10, a new channel type is:

1. One frozen `ChannelSpec` subclass in `src/nidaqlib/channels/<group>.py`
   with the NI-relevant fields (range, terminal config, scaling, units, safety
   metadata where applicable).
2. One backend handler — extend the `add_channel` dispatch in both
   `NidaqmxBackend` and `FakeDaqBackend`. Real backend wraps NI errors per
   design doc §16.4.
3. One fake-backend fixture exercising the spec round-trip and at least one
   error path.
4. If the channel writes (AO/DO/CO): a `confirm=True` gate at
   `DaqSession.write` and `safe_min` / `safe_max` validation **before** the
   backend call.

**Nothing else.** No new transport/protocol/command modules — those
intentionally do not exist (see design doc Appendix E).

## Safety

Any operation that writes to analog, digital, or counter outputs, or that can
damage hardware, must require `confirm=True` at the session entry point and
validate `safe_min` / `safe_max` before any backend call. See design doc §17.

## Commits

Conventional-style short prefixes are helpful but not mandatory:

- `feat:` new user-visible behaviour
- `fix:` bugfix
- `refactor:` internal cleanup
- `docs:` docs only
- `ci:` pipeline changes
- `chore:` tooling/version bumps

## Tests that need hardware

Mark them with `hardware`, `hardware_stateful`, `hardware_output`, or
`hardware_destructive`. These are skipped in CI by default. Stateful, output,
and destructive tiers also require opt-in env vars
(`NIDAQLIB_ENABLE_STATEFUL_TESTS=1`, `NIDAQLIB_ENABLE_OUTPUT_TESTS=1`,
`NIDAQLIB_ENABLE_DESTRUCTIVE_TESTS=1`).
