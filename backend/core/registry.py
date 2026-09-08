from __future__ import annotations

from collections.abc import Iterator
from threading import RLock
from typing import Generic, TypeVar


T = TypeVar("T")


class Registry(Generic[T]):
    """Small thread-safe registry used by extensible runtime components."""

    def __init__(self, label: str) -> None:
        self.label = label
        self._entries: dict[str, T] = {}
        self._lock = RLock()

    def register(self, key: str, value: T | None = None):
        normalized = key.strip().casefold()
        if not normalized:
            raise ValueError(f"Nome de {self.label} vazio.")

        def decorator(entry: T) -> T:
            with self._lock:
                if normalized in self._entries:
                    raise ValueError(f"{self.label.capitalize()} já registrado: {normalized}")
                self._entries[normalized] = entry
            return entry

        return decorator(value) if value is not None else decorator

    def replace(self, key: str, value: T) -> T:
        with self._lock:
            self._entries[key.strip().casefold()] = value
        return value

    def get(self, key: str) -> T:
        normalized = key.strip().casefold()
        with self._lock:
            try:
                return self._entries[normalized]
            except KeyError as exc:
                raise KeyError(f"{self.label.capitalize()} não encontrado: {key}") from exc

    def find(self, key: str) -> T | None:
        with self._lock:
            return self._entries.get(key.strip().casefold())

    def contains(self, key: str) -> bool:
        return self.find(key) is not None

    def items(self) -> tuple[tuple[str, T], ...]:
        with self._lock:
            return tuple(self._entries.items())

    def keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._entries)

    def values(self) -> tuple[T, ...]:
        with self._lock:
            return tuple(self._entries.values())

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

