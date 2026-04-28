"""Smoke test: package imports and exposes a version string."""

from __future__ import annotations


def test_import_version() -> None:
    import nidaqlib

    assert isinstance(nidaqlib.__version__, str)
    assert nidaqlib.__version__
