from jobflow_utils import normalize_status, is_valid_iso_date


def test_normalize_status_accepts_supported_statuses():
    assert normalize_status("Interview") == "Interview"
    assert normalize_status("Offer") == "Offer"


def test_normalize_status_defaults_unknown_values():
    assert normalize_status("unexpected") == "Applied"
    assert normalize_status("") == "Applied"
    assert normalize_status(None) == "Applied"


def test_iso_date_accepts_empty_optional_dates_and_valid_dates():
    assert is_valid_iso_date("") is True
    assert is_valid_iso_date(None) is True
    assert is_valid_iso_date("2026-10-12") is True


def test_iso_date_rejects_invalid_dates():
    assert is_valid_iso_date("12-10-2026") is False
    assert is_valid_iso_date("2026-02-30") is False
