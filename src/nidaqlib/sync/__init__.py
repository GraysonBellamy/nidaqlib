"""Sync facade — :class:`Daq`, :class:`SyncPortal`, sync recording wrappers.

Async is canonical; the sync facade wraps it through :class:`SyncPortal`
so scripts, notebooks, and REPL sessions can drive DAQ tasks without
``await``. Direct port of sartoriuslib's ``sync/`` package.
"""

from __future__ import annotations

from nidaqlib.streaming import ErrorPolicy, OverflowPolicy
from nidaqlib.sync.daq import Daq
from nidaqlib.sync.portal import SyncAsyncIterator, SyncPortal, run_sync
from nidaqlib.sync.recording import (
    AcquisitionSummary,
    record,
    record_polled,
)
from nidaqlib.sync.session import SyncDaqSession

__all__ = [
    "AcquisitionSummary",
    "Daq",
    "ErrorPolicy",
    "OverflowPolicy",
    "SyncAsyncIterator",
    "SyncDaqSession",
    "SyncPortal",
    "record",
    "record_polled",
    "run_sync",
]
