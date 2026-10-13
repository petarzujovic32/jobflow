"""Small validation helpers used by JobFlow routes."""
from datetime import datetime

VALID_STATUSES = frozenset({"Applied", "Interview", "Offer", "Rejected"})


def normalize_status(value: str | None) -> str:
    """Return a supported application status, defaulting to Applied."""
    status = (value or "").strip()
    return status if status in VALID_STATUSES else "Applied"


def is_valid_iso_date(value: str | None) -> bool:
    """Accept an empty optional date or a strict YYYY-MM-DD date."""
    if not value:
        return True
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False
