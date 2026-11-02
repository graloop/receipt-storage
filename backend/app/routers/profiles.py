# The household's profiles: who a receipt belongs to. There are no passwords;
# anyone on the home network picks a profile.

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import storage
from ..database import get_db
from ..models import Profile, Receipt
from ..schemas import ProfileCreate, ProfileOut, ProfileUpdate

router = APIRouter(prefix="/api/profiles", tags=["profiles"])

# New profiles take the next colour: emerald first, then ones that stay readable with white text.
PALETTE = ["#059669", "#2563eb", "#d97706", "#db2777", "#7c3aed", "#0891b2", "#dc2626", "#4d7c0f"]


def _receipt_count(db: Session, profile_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Receipt).where(Receipt.profile_id == profile_id))


def _out(db: Session, profile: Profile) -> ProfileOut:
    return ProfileOut(
        id=profile.id,
        name=profile.name,
        color=profile.color,
        email=profile.email,
        receipt_count=_receipt_count(db, profile.id),
    )


def _get_or_404(db: Session, profile_id: int) -> Profile:
    profile = db.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found.")
    return profile


def _name_taken(db: Session, name: str, ignore_id: int | None = None) -> bool:
    return any(p.id != ignore_id and p.name.casefold() == name.casefold() for p in db.scalars(select(Profile)))


def _require_free_email(db: Session, email: str | None, ignore_id: int | None = None) -> None:
    other = db.scalar(select(Profile).where(Profile.email == email)) if email else None
    if other is not None and other.id != ignore_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is already used by another profile.")


@router.get("", response_model=list[ProfileOut])
def list_profiles(db: Session = Depends(get_db)):
    return [_out(db, p) for p in db.scalars(select(Profile).order_by(Profile.created_at, Profile.id))]


@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
def create_profile(body: ProfileCreate, db: Session = Depends(get_db)):
    if _name_taken(db, body.name):
        raise HTTPException(status.HTTP_409_CONFLICT, "This name is already used by another profile.")
    _require_free_email(db, body.email)
    count = db.scalar(select(func.count()).select_from(Profile))
    profile = Profile(name=body.name, color=body.color or PALETTE[count % len(PALETTE)], email=body.email)
    db.add(profile)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "This name is already used by another profile.") from None
    return _out(db, profile)


@router.patch("/{profile_id}", response_model=ProfileOut)
def update_profile(profile_id: int, body: ProfileUpdate, db: Session = Depends(get_db)):
    profile = _get_or_404(db, profile_id)
    if body.name is not None:
        if _name_taken(db, body.name, ignore_id=profile.id):
            raise HTTPException(status.HTTP_409_CONFLICT, "This name is already used by another profile.")
        profile.name = body.name
    if body.color is not None:
        profile.color = body.color
    if "email" in body.model_fields_set:  # Sending it empty clears it.
        _require_free_email(db, body.email, ignore_id=profile.id)
        profile.email = body.email
    db.commit()
    return _out(db, profile)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    profile_id: int,
    delete_receipts: bool = Query(False, description="Required when the profile still has receipts."),
    db: Session = Depends(get_db),
):
    profile = _get_or_404(db, profile_id)
    receipts = list(db.scalars(select(Receipt).where(Receipt.profile_id == profile_id)).unique())
    # The page asks first and says how many; this guards against anything that doesn't.
    if receipts and not delete_receipts:
        raise HTTPException(status.HTTP_409_CONFLICT, "This profile still has receipts.")
    file_names = [r.image_path for r in receipts]
    for receipt in receipts:
        db.delete(receipt)
    db.delete(profile)
    db.commit()
    for name in file_names:
        storage.delete_file(name)
