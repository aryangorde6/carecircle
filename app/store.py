"""A minimal document-store interface with an in-memory implementation.

The domain layer talks only to :class:`DocumentStore`, so the same code runs
against Firestore in production and an in-memory dict in tests.
Collection paths look like ``"circles"`` or ``"circles/<id>/medications"``.
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Protocol

Document = dict[str, Any]


class DocumentStore(Protocol):
    """Operations the app needs from a document database."""

    def get(self, collection: str, doc_id: str) -> Document | None: ...

    def put(self, collection: str, doc_id: str, data: Document) -> None: ...

    def delete(self, collection: str, doc_id: str) -> None: ...

    def list(self, collection: str) -> list[Document]: ...

    def increment(self, collection: str, doc_id: str, field: str, amount: int = 1) -> int: ...


class InMemoryStore:
    """Thread-safe in-memory store used for local development and tests."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Document]] = {}
        self._lock = threading.Lock()

    def get(self, collection: str, doc_id: str) -> Document | None:
        with self._lock:
            doc = self._data.get(collection, {}).get(doc_id)
            return copy.deepcopy(doc) if doc is not None else None

    def put(self, collection: str, doc_id: str, data: Document) -> None:
        with self._lock:
            self._data.setdefault(collection, {})[doc_id] = copy.deepcopy(data)

    def delete(self, collection: str, doc_id: str) -> None:
        with self._lock:
            self._data.get(collection, {}).pop(doc_id, None)

    def list(self, collection: str) -> list[Document]:
        with self._lock:
            return [copy.deepcopy(doc) for doc in self._data.get(collection, {}).values()]

    def increment(self, collection: str, doc_id: str, field: str, amount: int = 1) -> int:
        with self._lock:
            doc = self._data.setdefault(collection, {}).setdefault(doc_id, {})
            doc[field] = int(doc.get(field, 0)) + amount
            return doc[field]
