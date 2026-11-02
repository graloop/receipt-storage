# The normal way to run the app: no API key. A receipt is saved right away and
# waits for the Claude extension in Chrome, which fills in the same form a person would.

import pytest

from app import extractor

from .conftest import png, upload


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    monkeypatch.setattr(extractor, "enabled", lambda: False)


def test_config_says_nothing_reads_at_upload(client):
    assert client.get("/api/config").json() == {"auto_read": False}


def test_upload_saves_it_unread_without_any_ai_or_error(client, profile, reader):
    result = upload(client, profile["id"], ("a.png", png()), source="photo")[0]
    assert result["status"] == "added"
    assert result["message"] is None
    assert result["receipt"]["unread"] is True
    assert result["receipt"]["source"] == "photo"
    assert reader.calls == 0
    assert client.get(f"/api/receipts/{result['receipt']['id']}/image").content == png()


def test_unread_list_and_filling_in_a_receipt_takes_it_off(client, profile):
    receipt = upload(client, profile["id"], ("a.png", png()))[0]["receipt"]
    assert [r["id"] for r in client.get("/api/receipts", params={"unread": "1"}).json()] == [receipt["id"]]

    filled = client.patch(
        f"/api/receipts/{receipt['id']}",
        json={
            "merchant": "Épicerie du Coin",
            "purchased_at": "2026-03-14",
            "total": 24.91,
            "category": "groceries",
            "items": [{"name": "Café moulu", "qty": 1, "price": 12.99}],
            "raw_text": "ÉPICERIE DU COIN Café moulu TOTAL 24,91",
            "needs_review": False,
        },
    ).json()
    assert filled["unread"] is False
    assert client.get("/api/receipts", params={"unread": "1"}).json() == []
    # And now it's found like any other.
    assert [r["id"] for r in client.get("/api/receipts", params={"q": "cafe"}).json()] == [receipt["id"]]


def test_a_person_saving_without_filling_it_in_also_takes_it_off(client, profile):
    receipt = upload(client, profile["id"], ("a.png", png()))[0]["receipt"]
    client.patch(f"/api/receipts/{receipt['id']}", json={"needs_review": False})
    assert client.get("/api/receipts", params={"unread": "1"}).json() == []


def test_a_half_read_receipt_still_counts_as_unread_until_it_has_a_merchant_or_total(client, profile):
    receipt = upload(client, profile["id"], ("a.png", png()))[0]["receipt"]
    # What a forwarded email will look like: a date and text, nothing else read yet.
    client.patch(f"/api/receipts/{receipt['id']}", json={"purchased_at": "2026-03-14", "raw_text": "text"})
    assert len(client.get("/api/receipts", params={"unread": "1"}).json()) == 1
    client.patch(f"/api/receipts/{receipt['id']}", json={"total": 5})
    assert client.get("/api/receipts", params={"unread": "1"}).json() == []


def test_the_model_property_and_the_search_agree(client, profile):
    unread = upload(client, profile["id"], ("a.png", png()))[0]["receipt"]
    other = upload(client, profile["id"], ("b.png", png("fake-restaurant.png")))[0]
    client.patch(f"/api/receipts/{other['receipt']['id']}", json={"merchant": "X", "needs_review": False})
    everything = client.get("/api/receipts").json()
    listed = {r["id"] for r in client.get("/api/receipts", params={"unread": "1"}).json()}
    assert {r["id"] for r in everything if r["unread"]} == listed == {unread["id"]}
