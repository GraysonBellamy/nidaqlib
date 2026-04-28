"""Testing utilities — :class:`FakeDaqBackend` re-export.

The fake backend lives at :mod:`nidaqlib.backend.fake` so internal module
graphs stay clean; this module is the user-facing surface mentioned in
the design doc's §28 v0.1 import list.
"""

from __future__ import annotations

from nidaqlib.backend.fake import FakeDaqBackend

__all__ = ["FakeDaqBackend"]
