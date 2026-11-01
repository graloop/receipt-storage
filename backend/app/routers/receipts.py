# Adding receipts from files, searching them, and changing or deleting one.

import hashlib
import re
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import extractor, storage
from ..database import fold, get_db
from ..models import Profile, Receipt
from ..schemas import Category, ReceiptOut, ReceiptUpdate, UploadResponse, UploadResult

router = APIRouter(prefix="/api/receipts", tags=["receipts"])


def _get_or_404(db: Session, receipt_id: int) -> Receipt:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found.")
    return receipt


def _require_profile(db: Session, profile_id: int) -> None:
    if db.get(Profile, profile_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found.")


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _currency(value: str | None) -> str:
    code = (value or "").strip().upper()
    return code if re.fullmatch(r"[A-Z]{3}", code) else "CAD"


def _source(kind: str, requested: str | None) -> str:
    if kind == "pdf":
        return "pdf"
    return "photo" if requested == "photo" else "screenshot"


def _duplicate(filename: str, existing: Receipt) -> UploadResult:
    return UploadResult(filename=filename, status="duplicate", receipt=ReceiptOut.model_validate(existing))


def _read_fields(extracted: extractor.ExtractedReceipt) -> dict:
    """What the model read, as a receipt's columns."""
    merchant = (extracted.merchant or "").strip() or None
    purchased_at = _parse_date(extracted.purchased_at)
    return {
        "merchant": merchant,
        "purchased_at": purchased_at,
        "total": extracted.total,
        "currency": _currency(extracted.currency),
        "category": extracted.category,
        "items": [item.model_dump() for item in extracted.items],
        "raw_text": extracted.raw_text,
        "needs_review": extracted.unsure or merchant is None or purchased_at is None or extracted.total is None,
    }


def _add_file(db: Session, upload: UploadFile, profile_id: int, requested_source: str | None) -> UploadResult:
    filename = upload.filename or "file"
    # One byte over the limit is enough to know it's too big.
    data = upload.file.read(storage.MAX_UPLOAD_BYTES + 1)
    message = None
    try:
        checked = storage.check_file(data)
        sha256 = hashlib.sha256(data).hexdigest()
        # Checked before reading, so the same photo twice doesn't cost a second API call.
        existing = db.scalar(select(Receipt).where(Receipt.file_sha256 == sha256))
        if existing is not None:
            return _duplicate(filename, existing)
        try:
            fields = _read_fields(extractor.read_receipt(data, checked.kind))
        except extractor.ExtractionError as err:
            # The paper may be in the trash already: keep the receipt, unread, whatever went wrong.
            fields = {"needs_review": True}
            message = str(err)
    except storage.FileRejected as err:
        return UploadResult(filename=filename, status="error", message=str(err))

    stored_name = storage.save_file(data, checked.extension)
    receipt = Receipt(
        profile_id=profile_id,
        source=_source(checked.kind, requested_source),
        image_path=stored_name,
        file_sha256=sha256,
        **fields,
    )
    db.add(receipt)
    try:
        db.commit()
    except IntegrityError:
        # The same file, sent twice at the same moment: the first one won.
        db.rollback()
        storage.delete_file(stored_name)
        return _duplicate(filename, db.scalar(select(Receipt).where(Receipt.file_sha256 == sha256)))
    return UploadResult(filename=filename, status="added", message=message, receipt=ReceiptOut.model_validate(receipt))


@router.post("/upload", response_model=UploadResponse)
def upload_receipts(
    profile_id: int = Form(...),
    source: str | None = Form(None),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    _require_profile(db, profile_id)
    return UploadResponse(results=[_add_file(db, upload, profile_id, source) for upload in files])


def _contains(column, text: str):
    """A LIKE that ignores case and accents, and takes % and _ as plain characters."""
    escaped = fold(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return func.fold(column).like(f"%{escaped}%", escape="\\")


@router.get("", response_model=list[ReceiptOut])
def search_receipts(
    q: str = "",
    profile_id: int | None = None,
    merchant: str = "",
    category: Category | None = None,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    query = select(Receipt)
    # A receipt with no date counts as dated the day it was added, so everything has a place in the order.
    sort_date = func.coalesce(Receipt.purchased_at, func.date(Receipt.created_at, "localtime"))
    # Every word has to appear somewhere: "amazon headphones" matches the merchant and an item.
    searchable = (
        func.coalesce(Receipt.merchant, "")
        + " "
        + func.item_names(Receipt.items)
        + " "
        + func.coalesce(Receipt.raw_text, "")
    )
    for word in q.split():
        query = query.where(_contains(searchable, word))
    if profile_id is not None:
        query = query.where(Receipt.profile_id == profile_id)
    if merchant.strip():
        query = query.where(_contains(Receipt.merchant, merchant.strip()))
    if category is not None:
        query = query.where(Receipt.category == category)
    if from_date is not None:
        query = query.where(sort_date >= from_date.isoformat())
    if to_date is not None:
        query = query.where(sort_date <= to_date.isoformat())
    query = query.order_by(sort_date.desc(), Receipt.id.desc())
    return list(db.scalars(query).unique())


@router.get("/{receipt_id}", response_model=ReceiptOut)
def get_receipt(receipt_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, receipt_id)


@router.patch("/{receipt_id}", response_model=ReceiptOut)
def update_receipt(receipt_id: int, body: ReceiptUpdate, db: Session = Depends(get_db)):
    receipt = _get_or_404(db, receipt_id)
    changes = body.model_dump(exclude_unset=True)
    # These can't be emptied: every receipt has an owner, a currency, a category and a list.
    for field in ("profile_id", "currency", "category", "items", "needs_review"):
        if field in changes and changes[field] is None:
            del changes[field]
    if "profile_id" in changes:
        _require_profile(db, changes["profile_id"])
    for field, value in changes.items():
        setattr(receipt, field, value)
    db.commit()
    db.refresh(receipt)
    return receipt


@router.post("/{receipt_id}/read", response_model=ReceiptOut)
def read_again(receipt_id: int, db: Session = Depends(get_db)):
    """For a receipt saved unread. One already read is refused, so corrections can't be overwritten."""
    receipt = _get_or_404(db, receipt_id)
    if not receipt.image_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This receipt has no file.")
    if receipt.raw_text:
        raise HTTPException(status.HTTP_409_CONFLICT, "This receipt was already read.")
    with open(storage.file_path(receipt.image_path), "rb") as f:
        data = f.read()
    try:
        fields = _read_fields(extractor.read_receipt(data, receipt.file_type))
    except (storage.FileRejected, extractor.ExtractionError) as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err)) from None
    for field, value in fields.items():
        setattr(receipt, field, value)
    db.commit()
    db.refresh(receipt)
    return receipt


@router.delete("/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_receipt(receipt_id: int, db: Session = Depends(get_db)):
    receipt = _get_or_404(db, receipt_id)
    file_name = receipt.image_path
    db.delete(receipt)
    db.commit()
    storage.delete_file(file_name)


@router.get("/{receipt_id}/image")
def get_receipt_image(receipt_id: int, db: Session = Depends(get_db)):
    receipt = _get_or_404(db, receipt_id)
    if not receipt.image_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This receipt has no file.")
    # nosniff: the browser must trust the type we checked, not guess from the content.
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"}
    if receipt.image_path.endswith(".heic"):
        return Response(storage.heic_as_jpeg(receipt.image_path), media_type="image/jpeg", headers=headers)
    extension = receipt.image_path[receipt.image_path.rfind(".") :]
    return FileResponse(
        storage.file_path(receipt.image_path), media_type=storage.MEDIA_TYPES[extension], headers=headers
    )
