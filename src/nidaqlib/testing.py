"""Testing utilities — fake backend re-export.

Re-exports :class:`~nidaqlib.backend.fake.FakeDaqBackend` for test code, and
aliases it as :class:`NIDaqFakeBackend` for cross-library naming symmetry
with the sibling libraries' fake-transport modules. The two names refer
to exactly the same class.
"""

from __future__ import annotations

from nidaqlib.backend.fake import FakeDaqBackend

NIDaqFakeBackend = FakeDaqBackend
"""Cross-library-symmetric alias for :class:`FakeDaqBackend`."""


__all__ = ["FakeDaqBackend", "NIDaqFakeBackend"]
