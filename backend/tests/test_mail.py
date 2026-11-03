# Forwarded receipts, through the app's real IMAP code and a fake mail server.

import imaplib
import pathlib
import time
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage

import pytest
from pydantic import SecretStr

from app import extractor, mail, storage
from app.config import get_settings

from .conftest import make_extracted, png
from .fake_imap import FakeImapServer, FakeMailbox

SENDER = "alex@example.com"
PDF = b"%PDF-1.4 fake invoice"


def forward(
    body="Thanks for your order",
    sender=SENDER,
    message_id="<1@example.com>",
    subject="Fwd: Your order",
    date_header="Sat, 01 Nov 2026 10:00:00 -0400",
    html=None,
    attachments=(),
    auth=None,
) -> bytes:
    message = EmailMessage()
    message["From"] = f"Alex <{sender}>"
    message["To"] = "receipts@example.com"
    message["Subject"] = subject
    message["Date"] = date_header
    if message_id:
        message["Message-ID"] = message_id
    if auth:
        message["Authentication-Results"] = auth
    if body is not None:
        message.set_content(body)
    if html:
        if body is None:
            message.set_content(html, subtype="html")
        else:
            message.add_alternative(html, subtype="html")
    for name, data, maintype, subtype in attachments:
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return message.as_bytes()


GMAIL_FORWARD = """FYI

---------- Forwarded message ---------
From: Shop <shop@nowhere.example>
Date: Sat, Mar 14, 2026 at 9:05 AM
Subject: Your order
To: Alex <alex@example.com>

Wireless headphones 89.99
Total 89.99"""


@pytest.fixture
def box(monkeypatch):
    mailbox = FakeMailbox()
    server = FakeImapServer(mailbox)
    settings = get_settings()
    monkeypatch.setattr(settings, "receipts_imap_user", "receipts@example.com")
    monkeypatch.setattr(settings, "receipts_imap_password", SecretStr("app-password"))
    monkeypatch.setattr(mail, "_connect", lambda: imaplib.IMAP4("127.0.0.1", server.port, timeout=10))
    # No key: the normal way to run. Forwarded receipts wait to be read.
    monkeypatch.setattr(extractor, "enabled", lambda: False)
    yield mailbox
    assert not mailbox.deleted, "the app did something to the mailbox other than marking mail as read"
    server.shutdown()
    server.server_close()


@pytest.fixture
def alex(client):
    return client.post("/api/profiles", json={"name": "Alex", "email": SENDER}).json()


def receipts(client):
    return client.get("/api/receipts").json()


def check(client):
    response = client.post("/api/mail/check")
    assert response.status_code == 200, response.text
    return response.json()["last_check"]


# --- Filing an email ---


def test_a_forward_waits_to_be_read_with_its_text_and_original_date(client, box, alex):
    uid = box.add(forward(GMAIL_FORWARD))
    result = check(client)
    assert result["added"] == 1
    receipt = receipts(client)[0]
    assert receipt["source"] == "email"
    assert receipt["profile_id"] == alex["id"]
    assert receipt["purchased_at"] == "2026-03-14"
    assert receipt["unread"] is True
    assert receipt["file_type"] is None
    assert "Wireless headphones" in receipt["raw_text"]
    assert "Subject: Fwd: Your order" in receipt["raw_text"]
    assert uid in box.seen
    assert box.logins == ["receipts@example.com"]


def test_it_can_be_found_before_being_read(client, box, alex):
    box.add(forward(GMAIL_FORWARD))
    check(client)
    assert len(client.get("/api/receipts", params={"q": "headphones"}).json()) == 1
    assert len(client.get("/api/receipts", params={"unread": "1"}).json()) == 1


def test_it_leaves_the_list_once_filled_in(client, box, alex):
    box.add(forward(GMAIL_FORWARD))
    check(client)
    receipt = receipts(client)[0]
    client.patch(f"/api/receipts/{receipt['id']}", json={"merchant": "Nowhere", "total": 89.99, "needs_review": False})
    assert client.get("/api/receipts", params={"unread": "1"}).json() == []


def test_no_original_date_falls_back_on_the_forward_date(client, box, alex):
    box.add(forward("No forwarded block here", date_header="Sat, 07 Feb 2026 10:00:00 +0000"))
    check(client)
    assert receipts(client)[0]["purchased_at"] == "2026-02-07"


def test_an_email_with_no_date_at_all_is_still_kept(client, box, alex):
    raw = forward("Text", date_header="not a date")
    box.add(raw)
    check(client)
    receipt = receipts(client)[0]
    assert receipt["purchased_at"] is None
    assert receipt["added_on"] == date.today().isoformat()


def test_a_pdf_attachment_is_kept_and_viewable(client, box, alex):
    box.add(forward("See attached", attachments=[("invoice.pdf", PDF, "application", "pdf")]))
    check(client)
    receipt = receipts(client)[0]
    assert receipt["file_type"] == "pdf"
    assert client.get(f"/api/receipts/{receipt['id']}/image").content == PDF
    assert "See attached" in receipt["raw_text"]


def test_an_image_attachment_is_kept(client, box, alex):
    box.add(forward("Photo", attachments=[("r.png", png(), "image", "png")]))
    check(client)
    assert receipts(client)[0]["file_type"] == "image"


def test_two_attachments_make_two_receipts(client, box, alex):
    box.add(
        forward(
            "Both",
            attachments=[("a.pdf", PDF, "application", "pdf"), ("b.pdf", PDF + b" 2", "application", "pdf")],
        )
    )
    assert check(client)["added"] == 1
    assert len(receipts(client)) == 2


def test_an_unusable_attachment_is_noted_and_the_email_kept(client, box, alex):
    box.add(forward("Body", attachments=[("evil.exe", b"MZ fake", "application", "octet-stream")]))
    check(client)
    receipt = receipts(client)[0]
    assert receipt["file_type"] is None
    assert "[Attachment not kept: evil.exe]" in receipt["raw_text"]


def test_a_forward_as_attachment_is_opened(client, box, alex):
    inner = EmailMessage()
    inner["From"] = "shop@nowhere.example"
    inner["Subject"] = "Order"
    inner.set_content("Inner text with the receipt")
    outer = EmailMessage()
    outer["From"] = SENDER
    outer["Subject"] = "Fwd"
    outer["Message-ID"] = "<outer@example.com>"
    outer.set_content("See attached email")
    outer.add_attachment(inner)
    box.add(outer.as_bytes())
    check(client)
    assert "Inner text with the receipt" in receipts(client)[0]["raw_text"]


def test_html_only_email_becomes_plain_text(client, box, alex):
    html = (
        "<html><head><style>p{color:red}</style><title>t</title></head><body>"
        "<p>Total: <b>12&nbsp;$</b></p><script>alert('x')</script><img src='http://track.example/a.png'>"
        "<table><tr><td>Caf&eacute;</td></tr></table></body></html>"
    )
    box.add(forward(None, html=html))
    check(client)
    text = receipts(client)[0]["raw_text"]
    assert "Total:" in text and "Café" in text
    for leftover in ("<", "alert", "color:red", "track.example"):
        assert leftover not in text


def test_table_cells_stay_apart_and_rows_on_their_own_lines(client, box, alex):
    html = "<table><tr><td>Burger</td><td>19.50</td></tr><tr><td>Total</td><td>40.03 CAD</td></tr></table>"
    box.add(forward(None, html=html))
    check(client)
    text = receipts(client)[0]["raw_text"]
    assert "Burger 19.50" in text
    assert "Total 40.03 CAD" in text
    assert "19.50 Total" not in text


# A receipt laid out the way shops do it, invented for these tests.
SHOP_HTML = """<html><body><table><tr><td>
<table><tr><td>Fake Runner 990</td><td><table><tr><td>$120.00</td></tr></table></td></tr></table>
<table><tr><td>Tax</td></tr><tr><td>$9.99</td></tr></table>
<table><tr><td>Order Total</td><td><table><tr><td><b>$129.99</b></td></tr></table></td></tr></table>
</td></tr></table></body></html>"""


def test_a_stub_plain_part_does_not_hide_the_receipt_in_the_html(client, box, alex):
    box.add(forward("Thanks for your order! View it in your browser: https://shop.example/o/1", html=SHOP_HTML))
    check(client)
    assert "Order Total $129.99" in receipts(client)[0]["raw_text"]


def test_a_plain_part_without_amounts_loses_to_html_with_them(client, box, alex):
    box.add(forward("Your order is on its way", html="<p>Paid: $5.00</p>"))
    check(client)
    assert "Paid: $5.00" in receipts(client)[0]["raw_text"]


def test_a_full_plain_part_is_still_preferred(client, box, alex):
    plain = "Fake Runner 990 $120.00\nTax $9.99\nOrder Total $129.99 (from the plain part)"
    box.add(forward(plain, html=SHOP_HTML))
    check(client)
    assert "(from the plain part)" in receipts(client)[0]["raw_text"]


def test_each_amount_lands_beside_its_own_label(client, box, alex):
    box.add(forward(None, html=SHOP_HTML))
    check(client)
    text = receipts(client)[0]["raw_text"]
    for line in ("Fake Runner 990 $120.00", "Tax $9.99", "Order Total $129.99"):
        assert line in text.splitlines()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Order Total\n\n$129.99", "Order Total $129.99"),
        ("Total payé\n129,99 $", "Total payé 129,99 $"),
        ("Total\nCAD 40.03", "Total CAD 40.03"),
        ("Total\n40.03 CAD", "Total 40.03 CAD"),
        ("Burger\n19.50\nTotal\n40.03", "Burger 19.50\nTotal 40.03"),
        # Not amounts, or no label to join: left as they are.
        ("Order number\n12345", "Order number\n12345"),
        ("Phone\n514-555-0100", "Phone\n514-555-0100"),
        ("Total $5.00\n$6.00", "Total $5.00\n$6.00"),
        ("$5.00\n$6.00", "$5.00\n$6.00"),
        ("First line\n\nSecond line", "First line\n\nSecond line"),
    ],
)
def test_join_amounts(text, expected):
    assert mail._join_amounts(text) == expected


def test_plain_text_is_preferred_over_html(client, box, alex):
    box.add(forward("The plain words", html="<p>The html words</p>"))
    check(client)
    assert "The plain words" in receipts(client)[0]["raw_text"]
    assert "html words" not in receipts(client)[0]["raw_text"]


def test_a_logo_inside_the_email_is_not_a_receipt(client, box, alex):
    message = EmailMessage()
    message["From"] = SENDER
    message["Message-ID"] = "<logo@example.com>"
    message.set_content("Text")
    message.add_alternative("<p>Text<img src='cid:logo'></p>", subtype="html")
    message.get_payload()[1].add_related(png(), maintype="image", subtype="png", cid="<logo>")
    box.add(message.as_bytes())
    check(client)
    assert [r["file_type"] for r in receipts(client)] == [None]


# --- Who may add receipts ---


def test_a_stranger_is_ignored_and_stays_unread(client, box, alex):
    uid = box.add(forward(sender="stranger@example.com"))
    assert check(client)["ignored"] == 1
    assert receipts(client) == []
    assert uid not in box.seen


def test_a_stranger_who_becomes_a_profile_is_filed_at_the_next_check(client, box, alex):
    box.add(forward(sender="sam@example.com"))
    check(client)
    client.post("/api/profiles", json={"name": "Sam", "email": "SAM@example.com"})
    assert check(client)["added"] == 1
    assert receipts(client)[0]["profile_name"] == "Sam"


def test_the_sender_goes_to_their_own_profile(client, box, alex):
    sam = client.post("/api/profiles", json={"name": "Sam", "email": "sam@example.com"}).json()
    box.add(forward(sender="SAM@example.com"))
    check(client)
    assert receipts(client)[0]["profile_id"] == sam["id"]


def test_a_forged_sender_is_ignored(client, box, alex):
    box.add(forward(auth="mx.google.com; dmarc=fail (p=NONE) header.from=example.com"))
    assert check(client)["ignored"] == 1
    assert receipts(client) == []


def test_an_honest_verdict_passes(client, box, alex):
    box.add(forward(auth="mx.google.com; dkim=pass; dmarc=pass header.from=example.com"))
    assert check(client)["added"] == 1


# --- Duplicates and failures ---


def test_the_same_email_twice_is_filed_once(client, box, alex):
    box.add(forward())
    box.add(forward())
    result = check(client)
    assert (result["added"], result["duplicate"]) == (1, 1)
    assert len(receipts(client)) == 1
    assert box.unseen() == []


def test_no_message_id_still_skips_the_same_email(client, box, alex):
    raw = forward(message_id=None)
    box.add(raw)
    box.add(raw)
    assert check(client)["duplicate"] == 1


def test_an_attachment_already_uploaded_as_a_photo_is_a_duplicate(client, box, alex):
    client.post(
        "/api/receipts/upload",
        data={"profile_id": str(alex["id"])},
        files=[("files", ("a.png", png()))],
    )
    box.add(forward("Same photo", attachments=[("a.png", png(), "image", "png")]))
    assert check(client)["duplicate"] == 1
    assert len(receipts(client)) == 1


def test_a_check_only_looks_at_new_mail(client, box, alex):
    box.add(forward())
    check(client)
    result = check(client)
    assert result["added"] == 0 and result["duplicate"] == 0


def test_one_bad_email_does_not_stop_the_others_and_stays_unread(client, box, alex, monkeypatch):
    bad = box.add(forward("boom", message_id="<bad@example.com>"))
    good = box.add(forward("fine", message_id="<good@example.com>"))
    real_save = mail.process

    def process(db, raw):
        if b"boom" in raw:
            raise RuntimeError("something odd in this email")
        return real_save(db, raw)

    monkeypatch.setattr(mail, "process", process)
    result = check(client)
    assert (result["added"], result["failed"]) == (1, 1)
    assert bad not in box.seen and good in box.seen


def test_a_wrong_password_is_reported_in_plain_words_and_recorded(client, box, alex, monkeypatch):
    monkeypatch.setattr(get_settings(), "receipts_imap_password", SecretStr("wrong"))
    result = check(client)
    assert result["error"] == "Couldn't check the mailbox. Check its address and app password."
    assert client.get("/api/mail").json()["last_check"]["error"]


def test_an_unreachable_server_is_recorded_not_a_crash(client, box, alex, monkeypatch):
    def refuse():
        raise OSError("connection refused")

    monkeypatch.setattr(mail, "_connect", refuse)
    assert "error" in check(client)


def test_failure_after_a_file_was_saved_leaves_nothing_behind(client, box, alex, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("database trouble")

    monkeypatch.setattr(mail, "Receipt", broken)
    box.add(forward("x", attachments=[("a.pdf", PDF, "application", "pdf")]))
    assert check(client)["failed"] == 1
    uploads = pathlib.Path(storage.UPLOADS_DIR)
    assert not uploads.exists() or list(uploads.iterdir()) == []


# --- With a key ---


def test_with_a_key_a_forward_is_read_at_once(client, box, alex, monkeypatch, reader):
    monkeypatch.setattr(extractor, "enabled", lambda: True)
    reader.next_answer = make_extracted(merchant="Read by the model", purchased_at=None, total=5.0)
    box.add(forward(GMAIL_FORWARD))
    check(client)
    receipt = receipts(client)[0]
    assert receipt["merchant"] == "Read by the model"
    assert receipt["purchased_at"] == "2026-03-14"  # The model found none: the email's date.
    assert receipt["unread"] is False


def test_with_a_key_a_failed_reading_still_keeps_the_email(client, box, alex, monkeypatch, reader):
    monkeypatch.setattr(extractor, "enabled", lambda: True)
    reader.next_answer = extractor.ExtractionError("Too many receipts at once. Try again in a minute.")
    box.add(forward(GMAIL_FORWARD))
    assert check(client)["added"] == 1
    receipt = receipts(client)[0]
    assert receipt["unread"] is True
    assert "Wireless headphones" in receipt["raw_text"]


# --- Once a day ---


def test_due_at_first_then_only_after_a_day(client, box, alex):
    assert mail.is_due() is True
    check(client)
    now = datetime.now(UTC)
    assert mail.is_due(now) is False
    assert mail.is_due(now + timedelta(hours=23)) is False
    assert mail.is_due(now + timedelta(hours=24, minutes=1)) is True


def test_a_failed_check_also_waits_for_tomorrow(client, box, alex, monkeypatch):
    monkeypatch.setattr(get_settings(), "receipts_imap_password", SecretStr("wrong"))
    check(client)
    assert mail.is_due() is False


def test_no_mailbox_set_means_no_thread_and_a_clear_answer(client):
    assert mail.configured() is False
    assert mail.start_daily_check() is None
    assert client.get("/api/mail").json() == {"configured": False, "address": None, "last_check": None}
    assert client.post("/api/mail/check").status_code == 409


def test_the_background_check_runs_once_and_stops(client, box, alex, monkeypatch):
    box.add(forward())
    monkeypatch.setattr(mail, "WAKE_UP_SECONDS", 0.01)
    stop = mail.start_daily_check()
    assert stop is not None
    deadline = time.time() + 5
    while time.time() < deadline and not receipts(client):
        time.sleep(0.02)
    stop.set()
    assert len(receipts(client)) == 1
    assert len(box.logins) == 1  # Many wake-ups, one check.


def test_two_checks_cannot_run_at_once(client, box, alex):
    assert mail._running.acquire(blocking=False)
    try:
        assert client.post("/api/mail/check").status_code == 409
    finally:
        mail._running.release()


def test_the_address_is_shown_but_never_the_password(client, box):
    state = client.get("/api/mail").json()
    assert state["address"] == "receipts@example.com"
    assert "app-password" not in str(state)
    assert "app-password" not in repr(get_settings())


# --- Reading the original date ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("---------- Forwarded message ---------\nFrom: x\nDate: Sat, Mar 14, 2026 at 9:05 AM", date(2026, 3, 14)),
        ("---------- Forwarded message ---------\nDe : x\nDate: sam. 14 mars 2026 à 09 h 05", date(2026, 3, 14)),
        ("---------- Message transféré ---------\nDe : x\nDate : 1er août 2025", date(2025, 8, 1)),
        ("Début du message transféré :\n\nDe: x\nDate: 2 février 2026 à 10:00:00 UTC−5", date(2026, 2, 2)),
        ("Begin forwarded message:\n\nFrom: x\nDate: December 3, 2024 at 10:00:00 AM EST", date(2024, 12, 3)),
        ("-----Original Message-----\nFrom: x\nSent: Tuesday, June 9, 2026 2:15 PM", date(2026, 6, 9)),
        ("-----Message d'origine-----\nDe : x\nEnvoyé : mardi 9 juin 2026 14:15", date(2026, 6, 9)),
        ("> ---------- Forwarded message ---------\n> From: x\n> Date: Jul 4, 2025", date(2025, 7, 4)),
        ("---------- Forwarded message ---------\nDate: 2026-03-14 10:00", date(2026, 3, 14)),
        ("---------- Forwarded message ---------\nDate: Feb 31, 2026", None),
        ("---------- Forwarded message ---------\nDate: soon", None),
        ("Date: Mar 14, 2026 but nothing was forwarded", None),
        ("", None),
    ],
)
def test_original_date(text, expected):
    assert mail.original_date(text) == expected
