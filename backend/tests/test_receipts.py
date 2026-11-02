import io
import pathlib
from datetime import date, timedelta

import pillow_heif
from PIL import Image

from app import extractor, storage

from .conftest import make_extracted, png, upload


def uploaded_files() -> list[pathlib.Path]:
    folder = pathlib.Path(storage.UPLOADS_DIR)
    return list(folder.iterdir()) if folder.exists() else []


def image_bytes(image_format: str = "PNG", size=(30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, format=image_format)
    return out.getvalue()


# --- Upload ---


def test_upload_saves_the_fields_and_the_file(client, profile):
    result = upload(client, profile["id"], ("store.png", png()), source="photo")[0]
    assert result["status"] == "added"
    receipt = result["receipt"]
    assert receipt["merchant"] == "Épicerie du Coin"
    assert receipt["purchased_at"] == "2026-03-14"
    assert receipt["total"] == 24.91
    assert receipt["source"] == "photo"
    assert receipt["file_type"] == "image"
    assert receipt["needs_review"] is False
    assert receipt["profile_name"] == "Alex"
    assert receipt["items"][0]["name"] == "Café moulu"
    assert len(uploaded_files()) == 1


def test_saved_file_has_a_random_name(client, profile):
    upload(client, profile["id"], ("../../evil name.png", png()))
    name = uploaded_files()[0].name
    assert "evil" not in name
    assert name.endswith(".png")


def test_upload_needs_a_real_profile(client):
    response = client.post("/api/receipts/upload", data={"profile_id": "99"}, files=[("files", ("a.png", png()))])
    assert response.status_code == 404
    assert uploaded_files() == []


def test_image_without_photo_source_is_a_screenshot(client, profile):
    assert upload(client, profile["id"], ("a.png", png()))[0]["receipt"]["source"] == "screenshot"


def test_pdf_is_recorded_as_pdf_whatever_the_button(client, profile, reader):
    result = upload(client, profile["id"], ("a.pdf", b"%PDF-1.4 fake"), source="photo")[0]
    assert result["receipt"]["source"] == "pdf"
    assert result["receipt"]["file_type"] == "pdf"
    assert uploaded_files()[0].suffix == ".pdf"


def test_iphone_heic_is_accepted_and_shown_as_jpeg(client, profile):
    heif = pillow_heif.from_pillow(Image.new("RGB", (40, 30), "white"))
    out = io.BytesIO()
    heif.save(out, format="HEIF")
    receipt = upload(client, profile["id"], ("a.heic", out.getvalue()))[0]["receipt"]
    assert uploaded_files()[0].suffix == ".heic"
    response = client.get(f"/api/receipts/{receipt['id']}/image")
    assert response.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(response.content)).format == "JPEG"


# --- needs_review ---


def test_review_when_the_model_is_unsure(client, profile, reader):
    reader.next_answer = make_extracted(unsure=True)
    assert upload(client, profile["id"], ("a.png", png()))[0]["receipt"]["needs_review"] is True


def test_review_when_a_key_field_is_missing(client, profile, reader):
    for i, missing in enumerate(({"merchant": None}, {"merchant": "   "}, {"purchased_at": None}, {"total": None})):
        reader.next_answer = make_extracted(**missing)
        data = image_bytes(size=(30 + i, 30))
        assert upload(client, profile["id"], ("a.png", data))[0]["receipt"]["needs_review"] is True


def test_impossible_date_is_dropped_and_flagged(client, profile, reader):
    reader.next_answer = make_extracted(purchased_at="2026-02-31")
    receipt = upload(client, profile["id"], ("a.png", png()))[0]["receipt"]
    assert receipt["purchased_at"] is None
    assert receipt["needs_review"] is True


def test_unknown_currency_falls_back_to_cad(client, profile, reader):
    reader.next_answer = make_extracted(currency=None)
    assert upload(client, profile["id"], ("a.png", png()))[0]["receipt"]["currency"] == "CAD"


# --- Duplicates and bad files ---


def test_same_file_twice_is_a_duplicate_and_not_read_again(client, profile, reader):
    first = upload(client, profile["id"], ("a.png", png()))[0]
    second = upload(client, profile["id"], ("copy.png", png()))[0]
    assert second["status"] == "duplicate"
    assert second["receipt"]["id"] == first["receipt"]["id"]
    assert reader.calls == 1
    assert len(uploaded_files()) == 1


def test_a_bad_file_does_not_stop_the_others(client, profile):
    results = upload(
        client,
        profile["id"],
        ("notes.txt", b"not a receipt"),
        ("good.png", png()),
        ("disguised.png", b"<script>alert(1)</script>"),
    )
    assert [r["status"] for r in results] == ["error", "added", "error"]
    assert results[0]["message"] == "This file isn't an image or a PDF."
    assert len(uploaded_files()) == 1


def test_svg_is_refused(client, profile):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert upload(client, profile["id"], ("a.svg", svg))[0]["status"] == "error"


def test_empty_file_is_refused(client, profile):
    assert upload(client, profile["id"], ("a.png", b""))[0]["message"] == "This file is empty."


def test_file_over_15_mb_is_refused_without_reading_it(client, profile, reader):
    big = b"%PDF-" + b"0" * (15 * 1024 * 1024)
    result = upload(client, profile["id"], ("big.pdf", big))[0]
    assert result["message"] == "This file is too big (15 MB maximum)."
    assert reader.calls == 0


def test_file_of_exactly_15_mb_is_accepted(client, profile):
    limit = b"%PDF-" + b"0" * (15 * 1024 * 1024 - 5)
    assert upload(client, profile["id"], ("limit.pdf", limit))[0]["status"] == "added"


def test_reading_failure_still_keeps_the_receipt_and_says_why(client, profile, reader):
    reader.next_answer = extractor.ExtractionError("Too many receipts at once. Try again in a minute.")
    result = upload(client, profile["id"], ("a.png", png()), source="photo")[0]
    assert result["status"] == "added"
    assert result["message"] == "Too many receipts at once. Try again in a minute."
    receipt = result["receipt"]
    assert receipt["needs_review"] is True
    assert receipt["raw_text"] is None
    assert receipt["source"] == "photo"
    assert receipt["category"] == "other"
    assert len(uploaded_files()) == 1
    assert client.get(f"/api/receipts/{receipt['id']}/image").content == png()
    assert [r["id"] for r in client.get("/api/receipts").json()] == [receipt["id"]]


def test_a_receipt_saved_unread_can_be_read_again(client, profile, reader):
    reader.next_answer = extractor.ExtractionError("Too many receipts at once. Try again in a minute.")
    receipt = upload(client, profile["id"], ("a.png", png()))[0]["receipt"]
    url = f"/api/receipts/{receipt['id']}/read"

    response = client.post(url)
    assert response.status_code == 502
    assert response.json()["detail"] == "Too many receipts at once. Try again in a minute."

    reader.next_answer = make_extracted()
    read = client.post(url).json()
    assert read["merchant"] == "Épicerie du Coin"
    assert read["needs_review"] is False
    assert read["id"] == receipt["id"]


def test_reading_again_cannot_overwrite_a_receipt_already_read(client, profile):
    receipt = upload(client, profile["id"], ("a.png", png()))[0]["receipt"]
    client.patch(f"/api/receipts/{receipt['id']}", json={"merchant": "Corrected by hand"})
    # conftest's fake reader would happily answer: the refusal has to come first.
    assert client.post(f"/api/receipts/{receipt['id']}/read").status_code == 409
    assert client.get(f"/api/receipts/{receipt['id']}").json()["merchant"] == "Corrected by hand"


# --- Search ---


def add(client, profile_id, index=0, **fields):
    extractor.read_receipt.next_answer = make_extracted(**fields)
    return upload(client, profile_id, (f"{index}.png", image_bytes(size=(30 + index, 30))))[0]["receipt"]


def merchants(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [r["merchant"] for r in response.json()]


def test_everything_is_in_date_order_newest_first(client, profile):
    add(client, profile["id"], 1, merchant="Middle", purchased_at="2025-06-15")
    add(client, profile["id"], 2, merchant="Oldest", purchased_at="2019-01-01")
    add(client, profile["id"], 3, merchant="Newest", purchased_at="2026-02-01")
    assert merchants(client.get("/api/receipts")) == ["Newest", "Middle", "Oldest"]


def test_order_follows_the_purchase_date_not_the_day_added_after_a_correction(client, profile):
    first = add(client, profile["id"], 1, merchant="A", purchased_at="2026-01-01")
    add(client, profile["id"], 2, merchant="B", purchased_at="2025-01-01")
    client.patch(f"/api/receipts/{first['id']}", json={"purchased_at": "2024-01-01"})
    assert merchants(client.get("/api/receipts")) == ["B", "A"]


def test_no_date_takes_its_place_by_the_day_it_was_added(client, profile):
    today = date.today()
    future = (today + timedelta(days=30)).isoformat()
    past = (today - timedelta(days=30)).isoformat()
    add(client, profile["id"], 1, merchant="Past", purchased_at=past)
    add(client, profile["id"], 2, merchant="Undated", purchased_at=None)
    add(client, profile["id"], 3, merchant="Future", purchased_at=future)
    assert merchants(client.get("/api/receipts")) == ["Future", "Undated", "Past"]
    assert client.get("/api/receipts").json()[1]["added_on"] == today.isoformat()


def test_date_filters_include_a_receipt_with_no_date_by_the_day_added(client, profile):
    today = date.today()
    add(client, profile["id"], 1, merchant="Undated", purchased_at=None)
    assert merchants(client.get("/api/receipts", params={"from": today.isoformat()})) == ["Undated"]
    tomorrow = (today + timedelta(days=1)).isoformat()
    assert merchants(client.get("/api/receipts", params={"from": tomorrow})) == []


def test_search_every_word_must_match_anywhere(client, profile):
    add(
        client,
        profile["id"],
        1,
        merchant="Amazon",
        items=[{"name": "Wireless headphones", "qty": 1, "price": 89.99}],
        raw_text="",
    )
    add(client, profile["id"], 2, merchant="Amazon", items=[{"name": "Book", "qty": 1, "price": 9.0}], raw_text="")
    add(client, profile["id"], 3, merchant="Bistro", items=[], raw_text="headphones repair")
    response = client.get("/api/receipts", params={"q": "amazon headphones"})
    assert len(response.json()) == 1
    assert response.json()[0]["items"][0]["name"] == "Wireless headphones"
    assert len(client.get("/api/receipts", params={"q": "headphones"}).json()) == 2


def test_search_ignores_accents_and_case_both_ways(client, profile):
    add(client, profile["id"], 1, merchant="ÉPICERIE Du Coin", items=[], raw_text="")
    add(client, profile["id"], 2, merchant="Cafe Noir", items=[], raw_text="")
    assert merchants(client.get("/api/receipts", params={"q": "epicerie"})) == ["ÉPICERIE Du Coin"]
    assert merchants(client.get("/api/receipts", params={"q": "café"})) == ["Cafe Noir"]


def test_search_finds_accented_item_names(client, profile):
    add(client, profile["id"], 1, items=[{"name": "Crème brûlée", "qty": 1, "price": 8.0}], raw_text="")
    assert len(client.get("/api/receipts", params={"q": "creme brulee"}).json()) == 1


def test_search_does_not_match_the_json_field_names(client, profile):
    add(client, profile["id"], 1, items=[{"name": "Book", "qty": 1, "price": 9.0}], raw_text="")
    for word in ("price", "qty", "name", "null"):
        assert client.get("/api/receipts", params={"q": word}).json() == []


def test_percent_and_underscore_are_plain_characters(client, profile):
    add(client, profile["id"], 1, merchant="Plain Shop", items=[], raw_text="")
    assert client.get("/api/receipts", params={"q": "%"}).json() == []
    assert client.get("/api/receipts", params={"q": "_"}).json() == []


def test_filters(client, profile):
    other = client.post("/api/profiles", json={"name": "Sam"}).json()
    add(client, profile["id"], 1, merchant="March", purchased_at="2026-03-10", category="groceries")
    add(client, profile["id"], 2, merchant="April", purchased_at="2026-04-10", category="restaurant")
    add(client, other["id"], 3, merchant="May", purchased_at="2026-05-10", category="groceries")

    def get(**params):
        return merchants(client.get("/api/receipts", params=params))

    assert get(profile_id=profile["id"]) == ["April", "March"]
    assert get(profile_id=other["id"]) == ["May"]
    assert get(category="groceries") == ["May", "March"]
    assert get(merchant="apr") == ["April"]
    # Both ends of a date range are included.
    assert get(**{"from": "2026-04-10"}) == ["May", "April"]
    assert get(to="2026-04-10") == ["April", "March"]
    assert get(**{"from": "2026-04-01", "to": "2026-04-30"}) == ["April"]


def test_bad_filter_values_are_refused(client):
    assert client.get("/api/receipts", params={"category": "nope"}).status_code == 422
    assert client.get("/api/receipts", params={"from": "yesterday"}).status_code == 422


# --- One receipt ---


def test_get_and_unknown(client, profile):
    receipt = add(client, profile["id"])
    assert client.get(f"/api/receipts/{receipt['id']}").json()["id"] == receipt["id"]
    assert client.get("/api/receipts/999").status_code == 404
    assert client.get("/api/receipts/999/image").status_code == 404


def test_patch_changes_only_what_is_sent_and_clears_review(client, profile, reader):
    reader.next_answer = make_extracted(unsure=True)
    receipt = upload(client, profile["id"], ("a.png", png()))[0]["receipt"]
    updated = client.patch(f"/api/receipts/{receipt['id']}", json={"total": 30.5, "needs_review": False}).json()
    assert updated["total"] == 30.5
    assert updated["needs_review"] is False
    assert updated["merchant"] == receipt["merchant"]


def test_patch_can_empty_optional_fields_but_not_required_ones(client, profile):
    receipt = add(client, profile["id"])
    body = {"purchased_at": None, "total": None, "merchant": None, "category": None, "currency": None}
    updated = client.patch(f"/api/receipts/{receipt['id']}", json=body).json()
    assert updated["purchased_at"] is None
    assert updated["total"] is None
    assert updated["merchant"] is None
    assert updated["category"] == receipt["category"]
    assert updated["currency"] == receipt["currency"]


def test_patch_validates(client, profile):
    receipt = add(client, profile["id"])
    url = f"/api/receipts/{receipt['id']}"
    assert client.patch(url, json={"category": "nope"}).status_code == 422
    assert client.patch(url, json={"currency": "DOLLARS"}).status_code == 422
    assert client.patch(url, json={"profile_id": 99}).status_code == 404
    assert client.patch(url, json={"currency": " usd "}).json()["currency"] == "USD"


def test_patch_cannot_touch_the_file_or_its_fingerprint(client, profile):
    receipt = add(client, profile["id"])
    before = uploaded_files()[0].name
    response = client.patch(f"/api/receipts/{receipt['id']}", json={"image_path": "../../.env", "file_sha256": "x"})
    assert response.status_code == 200
    assert client.get(f"/api/receipts/{receipt['id']}/image").status_code == 200
    assert uploaded_files()[0].name == before


def test_patch_can_move_a_receipt_to_another_profile(client, profile):
    other = client.post("/api/profiles", json={"name": "Sam"}).json()
    receipt = add(client, profile["id"])
    moved = client.patch(f"/api/receipts/{receipt['id']}", json={"profile_id": other["id"]}).json()
    assert moved["profile_id"] == other["id"]
    assert moved["profile_name"] == "Sam"


def test_delete_removes_the_row_and_the_file(client, profile):
    receipt = add(client, profile["id"])
    assert len(uploaded_files()) == 1
    assert client.delete(f"/api/receipts/{receipt['id']}").status_code == 204
    assert client.get(f"/api/receipts/{receipt['id']}").status_code == 404
    assert uploaded_files() == []
    assert client.delete(f"/api/receipts/{receipt['id']}").status_code == 404


def test_after_delete_the_same_file_can_be_added_again(client, profile):
    first = upload(client, profile["id"], ("a.png", png()))[0]["receipt"]
    client.delete(f"/api/receipts/{first['id']}")
    assert upload(client, profile["id"], ("a.png", png()))[0]["status"] == "added"


def test_image_route_serves_the_original_safely(client, profile):
    data = png()
    receipt = upload(client, profile["id"], ("a.png", data))[0]["receipt"]
    response = client.get(f"/api/receipts/{receipt['id']}/image")
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_uploads_are_not_reachable_any_other_way(client, profile):
    upload(client, profile["id"], ("a.png", png()))
    name = uploaded_files()[0].name
    for path in (f"/uploads/{name}", f"/data/uploads/{name}", f"/{name}"):
        assert client.get(path).status_code == 404
