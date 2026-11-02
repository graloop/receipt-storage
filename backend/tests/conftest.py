import os
import tempfile

# Configure the app before it's imported: settings and the engine are read at
# module import time. No API key: nothing here may call the real API.
_TMP_DIR = tempfile.mkdtemp(prefix="receipt-vault-tests-")
os.environ["DATA_DIR"] = _TMP_DIR
os.environ["ANTHROPIC_API_KEY"] = ""

import pathlib
import shutil

import pytest
from fastapi.testclient import TestClient

from app import extractor, storage
from app.database import Base, engine
from app.main import app

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def fresh_data():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    shutil.rmtree(storage.UPLOADS_DIR, ignore_errors=True)
    yield


def make_extracted(**overrides) -> extractor.ExtractedReceipt:
    fields = {
        "merchant": "Épicerie du Coin",
        "purchased_at": "2026-03-14",
        "total": 24.91,
        "currency": "CAD",
        "category": "groceries",
        "items": [{"name": "Café moulu", "qty": 1, "price": 12.99}],
        "raw_text": "ÉPICERIE DU COIN Café moulu 12,99 TOTAL 24,91",
        "unsure": False,
    }
    return extractor.ExtractedReceipt(**{**fields, **overrides})


class FakeReader:
    """Stands in for the model: answers `next_answer`, and counts the calls."""

    def __init__(self):
        self.next_answer = make_extracted()
        self.calls = 0

    def __call__(self, data, kind):
        self.calls += 1
        if isinstance(self.next_answer, Exception):
            raise self.next_answer
        return self.next_answer


@pytest.fixture(autouse=True)
def reader(monkeypatch):
    """Most tests act as if a key were set, with the fake answering. See test_no_key.py for the other way."""
    fake = FakeReader()
    monkeypatch.setattr(extractor, "read_receipt", fake)
    monkeypatch.setattr(extractor, "enabled", lambda: True)
    return fake


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def profile(client):
    return client.post("/api/profiles", json={"name": "Alex"}).json()


def png(name: str = "fake-grocery.png") -> bytes:
    return (FIXTURES / name).read_bytes()


def upload(client, profile_id, *files, source=None):
    """Sends (file name, bytes) pairs and answers the per-file results."""
    data = {"profile_id": str(profile_id)}
    if source:
        data["source"] = source
    response = client.post("/api/receipts/upload", data=data, files=[("files", f) for f in files])
    assert response.status_code == 200, response.text
    return response.json()["results"]
