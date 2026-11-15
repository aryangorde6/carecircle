"""Database round trips stay small and constant, whatever the date range."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime

from app.services.care import CareService
from app.services.demo import create_demo_circle
from app.store import Document, InMemoryStore


class CountingStore(InMemoryStore):
    """In-memory store that counts reads and writes per collection kind."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: Counter[str] = Counter()

    @staticmethod
    def _kind(collection: str) -> str:
        return collection.rsplit("/", 1)[-1]

    def list(self, collection: str) -> list[Document]:
        self.calls[f"list:{self._kind(collection)}"] += 1
        return super().list(collection)

    def put(self, collection: str, doc_id: str, data: Document) -> None:
        self.calls[f"put:{self._kind(collection)}"] += 1
        super().put(collection, doc_id, data)

    def put_many(self, collection: str, docs: dict[str, Document]) -> None:
        self.calls[f"put_many:{self._kind(collection)}"] += 1
        super().put_many(collection, docs)


NOW = datetime(2026, 11, 15, 5, 0, tzinfo=UTC)  # 10:30 in India


def test_demo_history_is_written_in_one_batch():
    store = CountingStore()
    create_demo_circle(CareService(store), NOW)
    assert store.calls["put_many:doses"] == 1
    assert store.calls["put:doses"] == 0
    assert len(store.list(next(c for c in store._data if c.endswith("/doses")))) > 20


def test_thirty_day_adherence_reads_each_collection_once():
    store = CountingStore()
    care = CareService(store)
    circle = create_demo_circle(care, NOW).circle
    store.calls.clear()
    overall, per_med = care.adherence(circle, date(2026, 10, 17), date(2026, 11, 15), NOW)
    assert overall.taken > 10 and set(per_med) == {"Amlodipine", "Atorvastatin", "Metformin"}
    assert store.calls["list:doses"] == 1
    assert store.calls["list:medications"] == 1


def test_insights_page_reads_doses_once(client, store, monkeypatch):
    from tests.conftest import csrf_from

    client.post("/demo", data={"csrf": csrf_from(client)})
    reads: Counter[str] = Counter()
    original = store.list

    def counting_list(collection: str) -> list[Document]:
        reads[collection.rsplit("/", 1)[-1]] += 1
        return original(collection)

    monkeypatch.setattr(store, "list", counting_list)
    assert client.get("/insights").status_code == 200
    assert reads["doses"] == 1
    assert client.get("/today").status_code == 200
    assert reads["doses"] == 2
