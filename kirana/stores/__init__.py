from __future__ import annotations

from .base import PlaceResult, StoreAdapter
from .handoff import HandoffStore
from .mock import MockStore


def get_adapter(store: str, kind: str = "auto", out_dir: str = "handoffs") -> StoreAdapter:
    """auto: the 'mock' store uses the mock adapter, every real store hands off."""
    if kind == "mock" or (kind == "auto" and store.lower() == "mock"):
        return MockStore()
    if kind in ("handoff", "auto"):
        return HandoffStore(out_dir)
    raise ValueError(f"unknown adapter: {kind}")


__all__ = ["get_adapter", "PlaceResult", "StoreAdapter", "HandoffStore", "MockStore"]
