import pathlib

from app import storage

from .conftest import png, upload


def test_create_and_list(client):
    assert client.get("/api/profiles").json() == []
    created = client.post("/api/profiles", json={"name": "  Alex   B  "})
    assert created.status_code == 201
    assert created.json()["name"] == "Alex B"
    assert created.json()["color"].startswith("#")
    assert [p["name"] for p in client.get("/api/profiles").json()] == ["Alex B"]


def test_each_new_profile_gets_another_colour(client):
    first = client.post("/api/profiles", json={"name": "A"}).json()
    second = client.post("/api/profiles", json={"name": "B"}).json()
    assert first["color"] != second["color"]


def test_names_are_unique_whatever_the_case(client):
    client.post("/api/profiles", json={"name": "Alex"})
    assert client.post("/api/profiles", json={"name": "ALEX"}).status_code == 409


def test_blank_name_is_refused(client):
    assert client.post("/api/profiles", json={"name": "   "}).status_code == 422


def test_colour_must_be_a_colour(client):
    assert client.post("/api/profiles", json={"name": "A", "color": "red;}"}).status_code == 422


def test_rename(client, profile):
    assert client.patch(f"/api/profiles/{profile['id']}", json={"name": "Sam"}).json()["name"] == "Sam"
    # Its own name, in another case, isn't "taken".
    assert client.patch(f"/api/profiles/{profile['id']}", json={"name": "SAM"}).status_code == 200


def test_rename_to_a_taken_name_is_refused(client, profile):
    client.post("/api/profiles", json={"name": "Sam"})
    assert client.patch(f"/api/profiles/{profile['id']}", json={"name": "sam"}).status_code == 409


def test_remove_empty_profile(client, profile):
    assert client.delete(f"/api/profiles/{profile['id']}").status_code == 204
    assert client.get("/api/profiles").json() == []


def test_removing_a_profile_with_receipts_needs_confirmation(client, profile):
    receipt = upload(client, profile["id"], ("a.png", png()))[0]["receipt"]
    assert client.get("/api/profiles").json()[0]["receipt_count"] == 1

    assert client.delete(f"/api/profiles/{profile['id']}").status_code == 409
    assert client.get(f"/api/receipts/{receipt['id']}").status_code == 200

    assert client.delete(f"/api/profiles/{profile['id']}?delete_receipts=true").status_code == 204
    assert client.get(f"/api/receipts/{receipt['id']}").status_code == 404


def test_removing_a_profile_removes_its_files(client, profile):
    upload(client, profile["id"], ("a.png", png()))
    uploads = pathlib.Path(storage.UPLOADS_DIR)
    assert len(list(uploads.iterdir())) == 1
    client.delete(f"/api/profiles/{profile['id']}?delete_receipts=true")
    assert list(uploads.iterdir()) == []


def test_unknown_profile(client):
    assert client.patch("/api/profiles/99", json={"name": "X"}).status_code == 404
    assert client.delete("/api/profiles/99").status_code == 404
