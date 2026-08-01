from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from packages.papers.object_store import PrivateObjectStore, StoredObject


def test_pdf_logs_and_exports_do_not_leak_private_material(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = PrivateObjectStore(root="/tmp/poliscope-test")
    task_id = uuid4()

    with caplog.at_level(logging.INFO):
        stored: StoredObject = store.store(task_id=task_id, content=b"secret-pdf")

    assert stored.object_key.startswith(f"tasks/{task_id}/")
    assert stored.encryption == "AES256"
    assert "secret-pdf" not in caplog.text
    assert "signed_url" not in stored.model_dump_json()
    assert b"secret-pdf" not in stored.model_dump_json().encode()


def test_stored_object_does_not_contain_raw_bytes() -> None:
    store = PrivateObjectStore(root="/tmp/poliscope-test")
    stored = store.store(task_id=uuid4(), content=b"another-secret")
    dto = store.public_dto(stored)
    assert "content" not in dto
    assert "bytes" not in dto
    assert "signed_url" not in dto


def test_object_key_includes_content_hash() -> None:
    import hashlib

    store = PrivateObjectStore(root="/tmp/poliscope-test")
    content = b"test-content-for-hash"
    stored = store.store(task_id=uuid4(), content=content)
    expected_hash = hashlib.sha256(content).hexdigest()
    assert expected_hash in stored.object_key
