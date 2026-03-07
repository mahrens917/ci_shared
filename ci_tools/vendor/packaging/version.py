"""Minimal implementation of packaging.version.Version."""

from __future__ import annotations

import re
from functools import total_ordering
from typing import Tuple

_VERSION_PATTERN = re.compile(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?")


class InvalidVersion(ValueError):
    """Raised when a version string cannot be parsed."""

    default_message = "Invalid version string"

    def __init__(self, *, detail: str | None = None) -> None:
        """Initialise the exception with optional extra detail."""
        message = self.default_message if detail is None else f"{self.default_message}: {detail}"
        super().__init__(message)


@total_ordering
class Version:
    """Very small subset of packaging.version.Version."""

    def __init__(self, version: str) -> None:
        match = _VERSION_PATTERN.match(version)
        if not match:
            raise InvalidVersion(detail=f"unable to parse {version!r}")
        groups = match.groups()
        release = [int(groups[0])]
        if groups[1] is not None:
            release.append(int(groups[1]))
        if groups[2] is not None:
            release.append(int(groups[2]))
        self._release: Tuple[int, ...] = tuple(release)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"Version('{self}')"

    def __str__(self) -> str:
        return ".".join(str(part) for part in self._release)

    @property
    def release(self) -> Tuple[int, ...]:
        """Return the release tuple (major, minor, patch)."""
        return self._release

    @property
    def major(self) -> int:
        """Return the major version number."""
        return self._release[0]

    @property
    def minor(self) -> int:
        """Return the minor version number."""
        return self._release[1] if len(self._release) > 1 else 0

    def _normalized_release(self) -> Tuple[int, int, int]:
        """Return a three-component tuple padded with zeros."""
        first = self._release[0] if len(self._release) > 0 else 0
        second = self._release[1] if len(self._release) > 1 else 0
        third = self._release[2] if len(self._release) > 2 else 0
        return (first, second, third)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            other = Version(str(other))
        return self._normalized_release() == other._normalized_release()

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            other = Version(str(other))
        return self._normalized_release() < other._normalized_release()

    def __hash__(self) -> int:  # pragma: no cover - deterministic
        return hash(self._normalized_release())
