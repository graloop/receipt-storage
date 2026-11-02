# What the Settings menu shows about the forwarded-receipts mailbox, and its
# "Check now" button: the automatic check runs once a day, too slow for trying it out.

from fastapi import APIRouter, HTTPException, status

from .. import mail
from ..config import get_settings

router = APIRouter(prefix="/api/mail", tags=["mail"])


def _state() -> dict:
    settings = get_settings()
    return {
        "configured": mail.configured(),
        "address": settings.receipts_imap_user if mail.configured() else None,
        "last_check": mail.last_check(),
    }


@router.get("")
def get_mail():
    return _state()


@router.post("/check")
def check_mail():
    if not mail.configured():
        raise HTTPException(status.HTTP_409_CONFLICT, "No mailbox is set up.")
    try:
        mail.check_now()
    except mail.MailError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err)) from None
    return _state()
