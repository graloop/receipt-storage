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
MAX_PER_CHECK = 200
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


def _part_text(part: EmailMessage) -> str:
    try:
        text = part.get_content()
    except (LookupError, ValueError):  # An unknown or wrong character set.
        text = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    return _html_to_text(text) if part.get_content_type() == "text/html" else text.strip()


def _contents(message: EmailMessage) -> tuple[str, list[tuple[str, bytes]]]:
    """The email's text, and its attached files. Logos inside the email don't count: they aren't attached."""
    body = message.get_body(preferencelist=("plain", "html"))
    texts = [_part_text(body)] if body is not None else []
    files = []
    for part in message.iter_attachments():
        if part.get_content_type() == "message/rfc822":  # "Forward as attachment".
            inner = part.get_content()
            inner_text, inner_files = _contents(inner)
            texts.append(inner_text)
            files.extend(inner_files)
        elif part.get_content_disposition() == "attachment":
            files.append((part.get_filename() or "file", part.get_payload(decode=True) or b""))
    return "\n\n".join(t for t in texts if t), files


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


def _is_forged(message: EmailMessage) -> bool:
    # Gmail adds its verdict on top, so the first header is the one to trust. Only
    # an explicit failure counts: plenty of honest domains have no such policy.
    return "dmarc=fail" in str(message.get("Authentication-Results", "")).lower()


# --- One email ---


def _profile_for(db: Session, message: EmailMessage) -> Profile | None:
    sender = parseaddr(str(message.get("From", "")))[1].casefold()
    if not sender or _is_forged(message):
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

    saved_files, new = [], 0
    try:
        for index, (data, checked) in enumerate(usable):
            sha256 = hashlib.sha256(data).hexdigest()
            if db.scalar(select(Receipt).where(Receipt.file_sha256 == sha256)) is not None:
                continue
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


def _collect(db: Session) -> dict:
    settings = get_settings()
    counts = {"added": 0, "duplicate": 0, "ignored": 0, "failed": 0}
    imap = _connect()
    try:
        imap.login(settings.receipts_imap_user, settings.receipts_imap_password.get_secret_value())
        imap.select("INBOX")
        _, found = imap.uid("SEARCH", None, "UNSEEN")
        for uid in found[0].split()[:MAX_PER_CHECK]:
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
            # Ignored mail stays unread too: it's filed once its sender's address is added to a profile.
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
