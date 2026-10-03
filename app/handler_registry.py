from __future__ import annotations

from collections.abc import Callable
from typing import Any


class LazyHandlerRegistry:
    def __init__(self, factories: dict[str, Callable[[], Any]]):
        self._factories = dict(factories)
        self._cache: dict[str, Any] = {}

    def register_factories(self, factories: dict[str, Callable[[], Any]]) -> None:
        self._factories.update(factories)

    def get(self, name: str) -> Any:
        if name not in self._cache:
            factory = self._factories[name]
            self._cache[name] = factory()
        return self._cache[name]

    def get_many(self, *names: str) -> tuple[Any, ...]:
        return tuple(self.get(name) for name in names)
