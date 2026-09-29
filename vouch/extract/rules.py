"""Rules baseline: pdfplumber text plus label-based regular expressions.

This is the approach most teams try first. It is fast, free and deterministic, and
it fails in predictable ways: a label it has never seen, or a figure that looks like
the total but isn't (a statement's "Amount Due" includes the previous balance).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pdfplumber

from vouch.extract import register
from vouch.schemas import Extraction, InvoiceFields

TITLE_WORDS = {"invoice", "tax invoice", "statement", "bill", "remit to:"}
NUM_LABELS = r"\b(?:invoice[ \t]*(?:no\.|no\b|number|#)|bill[ \t]*#|ref(?:erence)?\b)[ \t]*[:#]?[ \t]*"
DATE_LABELS = r"(?:invoice\s*date|issued(?:\s*\([^)]*\))?|date)\s*:?\s*"
TOTAL_LABELS = [r"(?<![-\w])total(?:\s+[A-Z]{3})?", r"amount\s+due", r"balance(?:\s*\([A-Z]{3}\))?"]
SUBTOTAL_LABELS = [r"sub-?\s*total", r"net\s+amount"]
TAX_LABELS = [r"(?:gst|hst|pst|vat|sales\s+tax)(?:\s*\(\d+%\))?"]
AMOUNT = r"(?:[A-Z]{3}\s*)?\$?\s*([\d,]+\.\d{2})"


def _amount_after(label_patterns: list[str], text: str) -> float | None:
    for lab in label_patterns:
        m = re.search(rf"(?im)^(?:.*?\s)?{lab}[ \t]*:?[ \t]*{AMOUNT}[ \t]*$", text)
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def _parse_date(raw: str, day_first: bool) -> date | None:
    raw = raw.strip().rstrip(".")
    fmts = ["%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y"]
    fmts += ["%d/%m/%Y", "%m/%d/%Y"] if day_first else ["%m/%d/%Y", "%d/%m/%Y"]
    for f in fmts:
        try:
            return datetime.strptime(raw, f).date()
        except ValueError:
            continue
    return None


def _vendor(page) -> str | None:
    """Largest-font line on the page that isn't a document title."""
    lines: dict[tuple[float, float], list] = {}
    for ch in page.chars:
        lines.setdefault((round(ch["top"]), round(ch["size"], 1)), []).append(ch)
    best, best_size = None, 0.0
    for (_, size), chars in lines.items():
        text = "".join(c["text"] for c in sorted(chars, key=lambda c: c["x0"])).strip()
        if text.lower() in TITLE_WORDS or not text:
            continue
        if size > best_size:
            best, best_size = text, size
    return best


def _currency(text: str) -> str | None:
    m = re.search(r"\b(CAD|USD|EUR|GBP)\b", text)
    return m.group(1) if m else None


class RulesExtractor:
    name = "rules"

    def extract(self, doc_id: str, pdf_path: Path) -> Extraction:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]
            text = page.extract_text() or ""
            vendor = _vendor(page)
        num = re.search(rf"(?i){NUM_LABELS}([A-Z0-9][\w/\-]*)", text)
        dm = re.search(rf"(?im){DATE_LABELS}([A-Za-z]+ \d{{1,2}}, \d{{4}}|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{4}})", text)
        day_first = bool(re.search(r"(?i)dd/mm", text))
        f = InvoiceFields(
            vendor_name=vendor,
            invoice_number=num.group(1) if num else None,
            invoice_date=_parse_date(dm.group(1), day_first) if dm else None,
            subtotal=_amount_after(SUBTOTAL_LABELS, text),
            tax=_amount_after(TAX_LABELS, text),
            total=_amount_after(TOTAL_LABELS, text),
            currency=_currency(text),
        )
        filled = sum(v is not None for v in f.model_dump().values())
        return Extraction(doc_id=doc_id, fields=f, confidence=filled / 7)


@register("rules")
def _factory(**_) -> RulesExtractor:
    return RulesExtractor()
