"""Firestore implementation of :class:`app.store.DocumentStore`.

Uses the Cloud Run service account through Application Default Credentials,
so no database key is stored anywhere.
"""

from __future__ import annotations

from google.cloud import firestore

from app.store import Document

# Firestore accepts at most 500 writes in one batch.
_BATCH_LIMIT = 500


class FirestoreStore:
    """Document store backed by Cloud Firestore (Native mode)."""

    def __init__(self, project: str | None = None) -> None:
        self._client = firestore.Client(project=project or None)

    def get(self, collection: str, doc_id: str) -> Document | None:
        snapshot = self._client.collection(collection).document(doc_id).get()
        return snapshot.to_dict() if snapshot.exists else None

    def put(self, collection: str, doc_id: str, data: Document) -> None:
        self._client.collection(collection).document(doc_id).set(data)

    def put_many(self, collection: str, docs: dict[str, Document]) -> None:
        items = list(docs.items())
        ref = self._client.collection(collection)
        for start in range(0, len(items), _BATCH_LIMIT):
            batch = self._client.batch()
            for doc_id, data in items[start : start + _BATCH_LIMIT]:
                batch.set(ref.document(doc_id), data)
            batch.commit()

    def delete(self, collection: str, doc_id: str) -> None:
        self._client.collection(collection).document(doc_id).delete()

    def list(self, collection: str) -> list[Document]:
        return [snap.to_dict() for snap in self._client.collection(collection).stream()]

    def increment(self, collection: str, doc_id: str, field: str, amount: int = 1) -> int:
        ref = self._client.collection(collection).document(doc_id)
        ref.set({field: firestore.Increment(amount)}, merge=True)
        snapshot = ref.get()
        return int((snapshot.to_dict() or {}).get(field, 0))
