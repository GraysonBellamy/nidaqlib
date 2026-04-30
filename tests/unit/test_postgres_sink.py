"""Light-touch tests for :class:`nidaqlib.sinks.PostgresSink`.

Exercises construction, config validation, and the lazy-import guard.
The real Postgres I/O paths are covered by ``nidaqlib[postgres]``
integration tests when asyncpg is installed (parity with watlowlib /
alicatlib / sartoriuslib).
"""

from __future__ import annotations

import pytest

from nidaqlib.sinks import PostgresConfig, PostgresSink


class TestPostgresConfigValidation:
    def test_requires_dsn_or_host(self) -> None:
        with pytest.raises(ValueError, match="dsn"):
            PostgresConfig()

    def test_dsn_and_host_mutually_exclusive(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            PostgresConfig(dsn="postgres://localhost/db", host="localhost")

    def test_rejects_bad_table_name(self) -> None:
        with pytest.raises(ValueError, match="table_samples"):
            PostgresConfig(host="localhost", table_samples="bad name")

    def test_rejects_bad_schema_name(self) -> None:
        with pytest.raises(ValueError, match="schema name"):
            PostgresConfig(host="localhost", schema="not safe")

    def test_rejects_negative_pool(self) -> None:
        with pytest.raises(ValueError, match="pool bounds"):
            PostgresConfig(host="localhost", pool_min_size=0)

    def test_target_does_not_leak_password(self) -> None:
        config = PostgresConfig(
            dsn="postgres://user:hunter2@db.example.com:5433/prod",
        )
        assert "hunter2" not in config.target()
        assert "db.example.com" in config.target()


class TestPostgresSinkConstruction:
    def test_sink_constructs_without_open(self) -> None:
        config = PostgresConfig(host="localhost", database="x", user="y")
        sink = PostgresSink(config)
        assert sink.config is config


@pytest.mark.anyio
async def test_open_raises_dependency_error_when_asyncpg_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If asyncpg is not installed, ``open`` raises ``NIDaqSinkDependencyError``."""
    import sys

    # Block the asyncpg import at module load time.
    monkeypatch.setitem(sys.modules, "asyncpg", None)
    sink = PostgresSink(PostgresConfig(host="localhost", database="x", user="y"))
    from nidaqlib.errors import NIDaqSinkDependencyError

    with pytest.raises(NIDaqSinkDependencyError, match=r"\[postgres\]"):
        await sink.open()
