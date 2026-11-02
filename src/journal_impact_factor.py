import logging
from pathlib import Path
from typing import Optional, Protocol, Union

import gspread
from google.oauth2.service_account import Credentials

from .logger import print_error

logger = logging.getLogger(__name__)

_SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1lP75APkxXAgT8aobV4UjTR51BpX9Ee0wgYA7tTd-zrM/edit?gid=0"
)
_CREDENTIALS_PATH = Path("google-credentials.json")


class Worksheet(Protocol):
    """Subset of the Google worksheet API used by this module."""

    def col_values(self, col: int) -> list[object]: ...

    def append_row(self, values: list[str]) -> object: ...


class UnavailableWorksheet:
    """Sentinel used after a failed worksheet initialization attempt."""


_UNAVAILABLE_WORKSHEET = UnavailableWorksheet()
_WorksheetCache = Union[Worksheet, UnavailableWorksheet, None]
_worksheet: _WorksheetCache = None


def _get_worksheet() -> Optional[Worksheet]:
    """Return the first worksheet, or ``None`` if credentials/sheet are unavailable."""
    global _worksheet
    if isinstance(_worksheet, UnavailableWorksheet):
        return None
    if _worksheet is not None:
        return _worksheet

    if not _CREDENTIALS_PATH.exists():
        print_error("google-credentials.json file not found")
        _worksheet = _UNAVAILABLE_WORKSHEET
        return None

    try:
        creds = Credentials.from_service_account_file(str(_CREDENTIALS_PATH), scopes=_SCOPE)
        client = gspread.authorize(creds)
        _worksheet = client.open_by_url(_SHEET_URL).sheet1
    except Exception as e:
        logger.warning("Journal impact factor sheet unavailable: %s", e)
        _worksheet = _UNAVAILABLE_WORKSHEET
        return None

    return _worksheet


def load_impact_factor() -> dict[str, str]:
    """
    Load the impact factor data from the Google Sheet and return it as a dictionary with lowercase keys.
    """
    sheet = _get_worksheet()
    if sheet is None:
        return {}

    journal_names = sheet.col_values(1)[1:]  # Column A (Journal names), excluding the header
    impact_factors = sheet.col_values(2)[1:]  # Column B (Impact factors), excluding the header

    # Extend lists to match length
    max_length = max(len(journal_names), len(impact_factors))
    journal_names.extend([None] * (max_length - len(journal_names)))
    impact_factors.extend([None] * (max_length - len(impact_factors)))

    # Create a dictionary with lowercase journal names as keys
    impact_factor_data = {
        str(journal_name).strip().lower(): "" if impact_factor is None else str(impact_factor)
        for journal_name, impact_factor in zip(journal_names, impact_factors)
        if journal_name and str(journal_name).strip()
    }

    return impact_factor_data


def add_impact_factor(journal_name: str, impact_factor: str) -> None:
    """
    Add a new journal name and impact factor to the Google Sheet.
    """
    sheet = _get_worksheet()
    if sheet is None:
        return
    sheet.append_row([journal_name, impact_factor])
