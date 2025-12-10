"""Minimal subset of packaging.specifiers used by the CI tooling."""

from __future__ import annotations

import re
from dataclasses import dataclass
from operator import eq, ge, gt, le, lt, ne
from typing import Iterable, Iterator, List, TypeVar

from ..._messages import format_default_message
from .version import InvalidVersion, Version

_SPEC_PATTERN = re.compile(r"\s*(===|==|!=|~=|>=|<=|>|<)\s*(.+)\s*$")
_STAR_PATTERN = re.compile(r"\*+")


class InvalidSpecifier(ValueError):
    """Raised when a version specifier cannot be parsed."""

    default_message = "Invalid version specifier"

    def __init__(self, *, detail: str | None = None) -> None:
        """Initialise the error with an optional detail string."""
        message = format_default_message(self.default_message, detail)
        super().__init__(message)

    @classmethod
    def for_value(cls, spec: str) -> "InvalidSpecifier":
        """Return an error describing the invalid spec string."""
        return cls(detail=f"unable to parse {spec!r}")

    @classmethod
    def unsupported_wildcard_operator(cls, operator: str) -> "InvalidSpecifier":
        """Return an error describing an unsupported wildcard operator."""
        return cls(detail=f"unsupported wildcard operator {operator!r}")

    @classmethod
    def unsupported_operator(cls, operator: str) -> "InvalidSpecifier":
        """Return an error describing an unsupported comparison operator."""
        return cls(detail=f"unsupported operator {operator!r}")


_T = TypeVar("_T")


@dataclass(frozen=True)
class Specifier:
    """Single version comparison constraint."""

    operator: str
    version: str

    def __init__(self, spec: str) -> None:
        """Parse the specifier string into operator and version components."""
        match = _SPEC_PATTERN.fullmatch(spec)
        if not match:
            raise InvalidSpecifier.for_value(spec)
        object.__setattr__(self, "operator", match.group(1))
        object.__setattr__(self, "version", match.group(2).strip())

    def __str__(self) -> str:
        return f"{self.operator}{self.version}"

    def _matches_wildcard(self, candidate: str) -> bool:
        """Return True when the wildcard operator matches the candidate."""
        prefix = _STAR_PATTERN.split(self.version, 1)[0]
        return candidate.startswith(prefix)

    def _handle_wildcard(self, candidate: str) -> bool:
        """Evaluate wildcard comparisons for equality and inequality."""
        op = self.operator
        if op == "==":
            return self._matches_wildcard(candidate)
        if op == "!=":
            return not self._matches_wildcard(candidate)
        raise InvalidSpecifier.unsupported_wildcard_operator(op)

    def _compare_versions(
        self,
        op: str,
        candidate_version: Version,
        spec_version: Version,
        raw_candidate: str,
    ) -> bool:
        standard_ops = {
            "==": eq,
            "!=": ne,
            ">": gt,
            ">=": ge,
            "<": lt,
            "<=": le,
        }

        if op == "~=":
            lower = spec_version
            upper = _compatible_upper_bound(spec_version)
            return lower <= candidate_version < upper
        if op == "===":
            return raw_candidate == self.version
        try:
            comparator = standard_ops[op]
        except KeyError as exc:  # pragma: no cover - defensive
            raise InvalidSpecifier.unsupported_operator(op) from exc
        return comparator(candidate_version, spec_version)

    def contains(self, candidate: str) -> bool:
        """Return True when *candidate* satisfies this specifier."""

        candidate = candidate.strip()
        op = self.operator
        if "*" in self.version:
            return self._handle_wildcard(candidate)

        candidate_version = Version(candidate)
        spec_version = Version(self.version)
        return self._compare_versions(op, candidate_version, spec_version, candidate)


def _compatible_upper_bound(version: Version) -> Version:
    """Return the upper bound for compatible release comparisons."""
    components = list(version.release)
    if len(components) >= 2:
        components[1] += 1
        return Version(".".join(str(part) for part in components[:2]))
    components[0] += 1
    return Version(str(components[0]))


class SpecifierSet:
    """Collection of Specifier objects enforcing all constraints."""

    _specs: List[Specifier]

    def __init__(self, specifiers: str = "") -> None:
        parts = [part.strip() for part in specifiers.split(",") if part.strip()]
        self._specs = [Specifier(part) for part in parts]

    def __iter__(self) -> Iterator[Specifier]:
        return iter(self._specs)

    def __len__(self) -> int:  # pragma: no cover - convenience
        return len(self._specs)

    def __bool__(self) -> bool:
        return bool(self._specs)

    def __str__(self) -> str:
        return ",".join(str(spec) for spec in self._specs)

    def filter(self, iterable: Iterable[_T], prereleases: bool | None = None) -> Iterator[_T]:
        """Yield items from *iterable* that satisfy every specifier."""

        del prereleases  # pragma: no cover - compatibility argument
        for item in iterable:
            candidate = _coerce_candidate(item)
            try:
                if all(spec.contains(candidate) for spec in self._specs):
                    yield item
            except InvalidVersion:
                continue


def _coerce_candidate(item: object) -> str:
    """Return the string representation used for comparisons."""
    if isinstance(item, str):
        return item
    if hasattr(item, "value"):
        return str(getattr(item, "value"))
    return str(item)
