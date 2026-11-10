# Collects forwarded receipts from the mailbox made only for them, once a day.
# The connection goes out from the server, so nothing is opened to the internet.
# It only reads that mailbox and marks what it took as read: it never sends or
# deletes anything. An email's content is data, kept as plain text, never run or
# shown as HTML.

import email
import hashlib
import imaplib
import logging
import re
import ssl
import threading
from datetime import UTC, date, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import extractor, storage
from .config import get_settings
from .database import SessionLocal, fold
from .models import AppState, Profile, Receipt
from .routers.receipts import read_fields

logger = logging.getLogger(__name__)

CHECK_EVERY = timedelta(hours=24)
WAKE_UP_SECONDS = 600  # How often the thread looks at the clock, not how often it checks the mail.
MAX_PER_CHECK = 200  # Emails from profiles opened per check; the rest wait for the next one.
HEADERS_PER_FETCH = 500
# A picture inside an email (not attached) is kept when it's this big, whatever the email says...
INLINE_PHOTO_BYTES = 300_000
# ...or this big when the email's text shows no amounts: a photo sent from a phone, not a shop's logo.
INLINE_PICTURE_BYTES = 20_000
MAX_TEXT_CHARS = 200_000
STATE_KEY = "mail_last_check"


class MailError(Exception):
    """The mailbox couldn't be checked. The message is shown to the person."""


def configured() -> bool:
    settings = get_settings()
    return bool(settings.receipts_imap_user and settings.receipts_imap_password.get_secret_value())


def _connect() -> imaplib.IMAP4:
    # A real certificate check: imaplib's default accepts any server.
    context = ssl.create_default_context()
    return imaplib.IMAP4_SSL(get_settings().receipts_imap_host, ssl_context=context, timeout=60)


# --- What's in an email ---


class _TextFromHtml(HTMLParser):
    """Only the words of an HTML email, with line breaks where the layout had them."""

    SKIPPED = {"script", "style", "head", "title"}
    BREAKS = {"br", "p", "div", "tr", "li", "table", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skipping = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIPPED:
            self.skipping += 1
        elif tag in self.BREAKS:
            self.parts.append("\n")
        elif tag in ("td", "th"):  # Most receipts are tables: keep "Total" apart from "40.03".
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIPPED:
            self.skipping = max(0, self.skipping - 1)
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    parser = _TextFromHtml()
    parser.feed(html)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# An amount alone on its line: "$129.99", "129,99 $", "CAD 40.03", "40.03 CAD".
_ONLY_AMOUNT = re.compile(r"^[-−]?\s*(?:[A-Z]{2,3}\s?)?[$€£]?\s?\d[\d\s.,]*(?:\s?(?:[$€£]|CAD|USD|EUR))?$")
_HAS_AMOUNT = re.compile(r"[$€£]\s?\d|\d\s?[$€£]|\d\s?(?:CAD|USD|EUR)\b|\d[.,]\d{2}\b")


def _is_amount(line: str) -> bool:
    return bool(_ONLY_AMOUNT.match(line) and _HAS_AMOUNT.search(line))


def _join_amounts(text: str) -> str:
    """Puts an amount back beside its label. Shops lay these out in nested tables, so
    "Order Total" and "$129.99" land on lines of their own, and the wrong number is easy to take."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        label = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
        if (
            label is not None
            and _is_amount(stripped)
            and re.search(r"[^\W\d_]", lines[label])
            and not _HAS_AMOUNT.search(lines[label])
        ):
            lines[label + 1 :] = []  # Only blank lines were in between.
            lines[label] = f"{lines[label].rstrip()} {stripped}"
        else:
            lines.append(line)
    return "\n".join(lines)


def _part_text(part: EmailMessage) -> str:
    try:
        text = part.get_content()
    except (LookupError, ValueError):  # An unknown or wrong character set.
        text = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    return _join_amounts(_html_to_text(text) if part.get_content_type() == "text/html" else text.strip())


def _body_text(message: EmailMessage) -> str:
    """Plain text reads best, but some shops' is only "view this email in your browser", with
    the receipt in the HTML version alone. Then the HTML version is the one to keep."""
    plain_part = message.get_body(preferencelist=("plain",))
    html_part = message.get_body(preferencelist=("html",))
    plain = _part_text(plain_part) if plain_part is not None else ""
    html = _part_text(html_part) if html_part is not None else ""
    if not plain:
        return html
    lost_the_amounts = _HAS_AMOUNT.search(html) and not _HAS_AMOUNT.search(plain)
    return html if lost_the_amounts or len(plain) * 3 < len(html) else plain


def _inline_pictures(message: EmailMessage, text: str) -> list[tuple[str, bytes]]:
    """Pictures placed inside the email rather than attached, as phone mail apps do with a photo.
    Shops' emails are full of them too (logos, products), so a small one only counts when the
    text has no amounts, and a photo-sized one always does."""
    pictures = []
    for part in message.walk():
        if part.get_content_maintype() != "image" or part.get_content_disposition() == "attachment":
            continue
        data = part.get_payload(decode=True) or b""
        if len(data) >= INLINE_PHOTO_BYTES or (len(data) >= INLINE_PICTURE_BYTES and not _HAS_AMOUNT.search(text)):
            pictures.append((part.get_filename() or "photo", data))
    return pictures


def _contents(message: EmailMessage) -> tuple[str, list[tuple[str, bytes]]]:
    """The email's text, and its files: attachments, and pictures placed inside it that look like receipts."""
    texts = [_body_text(message)]
    files = []
    for part in message.iter_attachments():
        if part.get_content_type() == "message/rfc822":  # "Forward as attachment".
            inner = part.get_content()
            inner_text, inner_files = _contents(inner)
            texts.append(inner_text)
            files.extend(inner_files)
        elif part.get_content_disposition() == "attachment":
            files.append((part.get_filename() or "file", part.get_payload(decode=True) or b""))
    text = "\n\n".join(t for t in texts if t)
    return text, files + _inline_pictures(message, text)


_FORWARD_MARK = re.compile(r"forwarded message|message transfere|message d'origine|original message")
_DATE_LINE = re.compile(r"^[>\s]*(?:date|sent|envoye)\s*:\s*(.+)$")
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "fev": 2,
    "mar": 3,
    "apr": 4,
    "avr": 4,
    "may": 5,
    "mai": 5,
    "jun": 6,
    "juin": 6,
    "jul": 7,
    "juil": 7,
    "aug": 8,
    "aou": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _month(name: str) -> int | None:
    return _MONTHS.get(name[:4]) or _MONTHS.get(name[:3])


def _date_in(text: str) -> date | None:
    """Reads "Sat, Mar 14, 2026 at 9:05 AM", "sam. 14 mars 2026" or "2026-03-14"."""
    try:
        if found := re.search(r"(\d{4})-(\d{2})-(\d{2})", text):
            return date(int(found[1]), int(found[2]), int(found[3]))
        if (found := re.search(r"(\d{1,2})(?:er)?\s+([a-z]+)\.?,?\s+(\d{4})", text)) and _month(found[2]):
            return date(int(found[3]), _month(found[2]), int(found[1]))
        if (found := re.search(r"([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", text)) and _month(found[1]):
            return date(int(found[3]), _month(found[1]), int(found[2]))
    except ValueError:  # Not a real day.
        pass
    return None


def original_date(text: str) -> date | None:
    """The date the original email was sent, from the block a forward starts with."""
    lines = fold(text).splitlines()
    for start, line in enumerate(lines):
        if _FORWARD_MARK.search(line):
            for candidate in lines[start + 1 : start + 12]:
                if found := _DATE_LINE.match(candidate):
                    return _date_in(found[1])
            return None
    return None


def _sent_date(message: EmailMessage) -> date | None:
    try:
        sent = message["Date"].datetime
    except (AttributeError, TypeError, ValueError):
        return None
    return sent.astimezone().date() if sent else None


def _sender_proven(message: EmailMessage) -> bool:
    # Anyone can write any address in "From". Gmail checks it (DMARC) and adds its verdict
    # on top, so the first header is the one to trust; anything below it could be forged.
    # Only a pass counts: no verdict at all proves nothing. Gmail, Outlook and iCloud all pass.
    return "dmarc=pass" in str(message.get("Authentication-Results", "")).lower()


# --- One email ---


def _profile_for(db: Session, message: EmailMessage) -> Profile | None:
    sender = parseaddr(str(message.get("From", "")))[1].casefold()
    if not sender or not _sender_proven(message):
        return None
    return db.scalar(select(Profile).where(Profile.email == sender))


def _fields(data: bytes, kind: str, sent: date | None) -> dict:
    """Unread, dated by the email, unless a key lets the model read it now."""
    fields = {"needs_review": True, "purchased_at": sent}
    if extractor.enabled():
        try:
            fields = read_fields(extractor.read_receipt(data, kind))
            fields["purchased_at"] = fields["purchased_at"] or sent
        except (storage.FileRejected, extractor.ExtractionError) as err:
            logger.warning("An email couldn't be read now, it waits to be read: %s", err)
    return fields


def process(db: Session, raw: bytes) -> str:
    """Files one email. Answers "added", "duplicate" or "ignored" (not from a profile)."""
    message = email.message_from_bytes(raw, policy=policy.default)
    profile = _profile_for(db, message)
    if profile is None:
        return "ignored"
    message_id = str(message.get("Message-ID", "")).strip() or hashlib.sha256(raw).hexdigest()
    if db.scalar(select(Receipt).where(Receipt.email_message_id == message_id)) is not None:
        return "duplicate"

    text, attachments = _contents(message)
    usable, notes = [], []
    for name, data in attachments:
        try:
            usable.append((data, storage.check_file(data)))
        except storage.FileRejected:
            notes.append(f"[Attachment not kept: {name}]")
    subject = " ".join(str(message.get("Subject", "")).split())
    raw_text = "\n".join([f"Subject: {subject}", text, *notes]).strip()[:MAX_TEXT_CHARS]
    sent = original_date(text) or _sent_date(message)

    saved_files, new, seen = [], 0, set()
    try:
        for index, (data, checked) in enumerate(usable):
            sha256 = hashlib.sha256(data).hexdigest()
            # The same file twice in one email, or already in the app: kept once.
            if sha256 in seen or db.scalar(select(Receipt).where(Receipt.file_sha256 == sha256)) is not None:
                continue
            seen.add(sha256)
            fields = _fields(data, checked.kind, sent)
            fields.setdefault("raw_text", raw_text)
            stored_name = storage.save_file(data, checked.extension)
            saved_files.append(stored_name)
            db.add(
                Receipt(
                    profile_id=profile.id,
                    source="email",
                    image_path=stored_name,
                    file_sha256=sha256,
                    # One email can hold several receipts; each needs its own unique id.
                    email_message_id=message_id if index == 0 else f"{message_id}#{index}",
                    **fields,
                )
            )
            new += 1
        if not usable:
            fields = _fields(raw_text.encode(), "text", sent)
            fields.setdefault("raw_text", raw_text)
            db.add(Receipt(profile_id=profile.id, source="email", email_message_id=message_id, **fields))
            new += 1
        db.commit()
    except IntegrityError:
        db.rollback()
        for name in saved_files:
            storage.delete_file(name)
        return "duplicate"
    except Exception:
        db.rollback()
        for name in saved_files:
            storage.delete_file(name)
        raise
    return "added" if new else "duplicate"


# --- The mailbox ---


def _sender_headers(imap: imaplib.IMAP4, uids: list[bytes]):
    """Who sent each unread email and Gmail's verdict on it, without downloading the emails.
    Only those from a profile are opened, so a pile of mail from strangers, which stays
    unread, can never hide a receipt behind it."""
    for start in range(0, len(uids), HEADERS_PER_FETCH):
        batch = b",".join(uids[start : start + HEADERS_PER_FETCH])
        _, parts = imap.uid("FETCH", batch, "(BODY.PEEK[HEADER.FIELDS (FROM AUTHENTICATION-RESULTS)])")
        for part in parts:
            if isinstance(part, tuple) and (found := re.search(rb"UID (\d+)", part[0])):
                yield found[1], part[1]


def _collect(db: Session) -> dict:
    settings = get_settings()
    counts = {"added": 0, "duplicate": 0, "ignored": 0, "failed": 0}
    imap = _connect()
    try:
        imap.login(settings.receipts_imap_user, settings.receipts_imap_password.get_secret_value())
        imap.select("INBOX")
        _, found = imap.uid("SEARCH", None, "UNSEEN")
        wanted = []
        for uid, headers in _sender_headers(imap, found[0].split()):
            if _profile_for(db, email.message_from_bytes(headers, policy=policy.default)) is None:
                counts["ignored"] += 1
            else:
                wanted.append(uid)
        for uid in wanted[:MAX_PER_CHECK]:
            # PEEK: fetching doesn't mark it as read. Only what was filed is marked.
            _, parts = imap.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next(part[1] for part in parts if isinstance(part, tuple))
            try:
                outcome = process(db, raw)
            except Exception:
                # One odd email mustn't stop the others. It stays unread, for tomorrow's check.
                logger.exception("Couldn't file an email")
                counts["failed"] += 1
                continue
            counts[outcome] += 1
            # Ignored mail stays unread: it's filed once its sender's address is added to a profile.
            if outcome != "ignored":
                imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
    return counts


def _record(db: Session, result: dict) -> dict:
    state = db.get(AppState, STATE_KEY) or AppState(key=STATE_KEY)
    state.value = {"at": datetime.now(UTC).isoformat(), **result}
    db.add(state)
    db.commit()
    return state.value


_running = threading.Lock()


def check_now() -> dict:
    """One check of the mailbox, whatever the time. Answers what happened, also kept for the Settings menu."""
    if not _running.acquire(blocking=False):
        raise MailError("The mailbox is being checked right now.")
    try:
        with SessionLocal() as db:
            try:
                return _record(db, _collect(db))
            except (imaplib.IMAP4.error, OSError):
                db.rollback()
                logger.warning("The mailbox check failed")
                return _record(db, {"error": "Couldn't check the mailbox. Check its address and app password."})
    finally:
        _running.release()


def last_check() -> dict | None:
    with SessionLocal() as db:
        state = db.get(AppState, STATE_KEY)
        return state.value if state else None


def is_due(now: datetime | None = None) -> bool:
    """A failed check counts too: it's tried again the next day, like the others."""
    last = last_check()
    if last is None:
        return True
    return (now or datetime.now(UTC)) - datetime.fromisoformat(last["at"]) >= CHECK_EVERY


def _daily_loop(stop: threading.Event) -> None:
    while True:
        try:
            if is_due():
                check_now()
        except Exception:
            logger.exception("The daily mail check failed")
        # Compared with the saved time, so restarting the app doesn't add checks.
        if stop.wait(WAKE_UP_SECONDS):
            return


def start_daily_check() -> threading.Event | None:
    """Runs in the background while the app is on. Answers what stops it, or None with no mailbox set."""
    if not configured():
        return None
    stop = threading.Event()
    threading.Thread(target=_daily_loop, args=(stop,), name="mail-daily-check", daemon=True).start()
    return stop
