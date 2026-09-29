"""Match extracted invoices to ledger entries and classify every exception.

Scoring is deliberately explainable: each candidate pair gets points for reference,
vendor, amount and date evidence, and the reason string says which fired. An auditor
has to be able to see why the tool linked two items.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from vouch.schemas import Extraction, InvoiceFields, LedgerEntry, Link, MatchResult, Status

SUFFIXES = r"\b(ltd|limited|inc|incorporated|corp|corporation|co|company|llp|llc|services)\b\.?"
DATE_WINDOW = (-60, 75)       # posting minus invoice date, days, for a plausible pairing
MIN_SCORE = 5.0


def norm_ref(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def norm_vendor(s: str | None) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(SUFFIXES, " ", s)
    return re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\s+", " ", s)).strip()


def vendor_sim(a: str | None, b: str | None) -> float:
    return fuzz.token_set_ratio(norm_vendor(a), norm_vendor(b))


def is_transposition(a: float, b: float) -> bool:
    """Same digits, one adjacent pair swapped (difference is a multiple of 9)."""
    sa, sb = f"{a:.2f}", f"{b:.2f}"
    if len(sa) != len(sb) or sa == sb:
        return False
    diff = [i for i in range(len(sa)) if sa[i] != sb[i]]
    return len(diff) == 2 and diff[1] == diff[0] + 1 and sa[diff[0]] == sb[diff[1]] and sa[diff[1]] == sb[diff[0]]


def load_ledger(path: Path) -> list[LedgerEntry]:
    with open(path, newline="") as f:
        return [LedgerEntry(**{**r, "amount": float(r["amount"])}) for r in csv.DictReader(f)]


@dataclass
class Candidate:
    doc_id: str
    entry: LedgerEntry
    score: float
    amount_ok: bool
    amount_variant: str | None
    reasons: list[str] = field(default_factory=list)


def score(doc_id: str, f: InvoiceFields, e: LedgerEntry) -> Candidate | None:
    reasons: list[str] = []
    s = 0.0
    r_doc, r_led = norm_ref(f.invoice_number), norm_ref(e.reference)
    ref_exact = bool(r_doc) and r_doc == r_led
    ref_close = bool(r_doc and r_led) and not ref_exact and Levenshtein.distance(r_doc, r_led) == 1
    vs = vendor_sim(f.vendor_name, e.vendor_name)

    amount_ok = f.total is not None and abs(f.total - e.amount) < 0.011
    variant = None
    if f.total is not None and not amount_ok:
        if f.subtotal is not None and abs(f.subtotal - e.amount) < 0.011 and f.tax:
            variant = "posted net of tax"
        elif is_transposition(f.total, e.amount):
            variant = "transposed digits"

    lag = (e.posting_date - f.invoice_date).days if f.invoice_date else None
    in_window = lag is not None and DATE_WINDOW[0] <= lag <= DATE_WINDOW[1]

    if ref_exact:
        s += 6; reasons.append("reference matches")
    elif ref_close:
        s += 2; reasons.append("reference one character off")
    if vs >= 90:
        s += 3; reasons.append("vendor matches")
    elif vs >= 75:
        s += 1.5; reasons.append(f"vendor similar ({vs:.0f})")
    elif not ref_exact:
        return None                                   # different vendor and no reference: not a pair
    if amount_ok:
        s += 4; reasons.append("amount matches")
    elif variant:
        s += 2; reasons.append(f"amount differs ({variant})")
    if in_window:
        s += 1.5 - min(abs(lag), 60) / 60; reasons.append(f"posted {lag:+d} days from invoice")
    elif lag is not None:
        s -= 1

    # a close reference alone is weak evidence (neighbouring invoice numbers are common)
    if ref_close and not (amount_ok or variant):
        return None
    if not ref_exact and not (amount_ok or variant):
        return None
    if s < MIN_SCORE:
        return None
    return Candidate(doc_id, e, s, amount_ok, variant, reasons)


def fiscal_year(d: date) -> int:
    return d.year  # calendar fiscal year; change here for other year-ends


def match(extractions: dict[str, Extraction], ledger: list[LedgerEntry],
          review_below: float = 0.0) -> MatchResult:
    """Greedy one-to-one assignment by score, then a second pass for duplicate postings."""
    docs = {k: v.fields for k, v in extractions.items()}
    needs_review = sorted(k for k, v in extractions.items()
                          if v.confidence < review_below or v.fields.total is None)
    cands = [c for d, f in docs.items() for e in ledger if (c := score(d, f, e))]
    cands.sort(key=lambda c: (-c.score, c.entry.posting_date, c.entry.entry_id))

    used_docs: dict[str, Candidate] = {}
    used_entries: set[str] = set()
    links: list[Link] = []
    for c in cands:
        if c.doc_id in used_docs or c.entry.entry_id in used_entries:
            continue
        used_docs[c.doc_id] = c
        used_entries.add(c.entry.entry_id)

    # Duplicate postings: an unused entry that fully matches an already-linked document
    # (same vendor, same amount, and either the same reference or no reference at all).
    for c in cands:
        e = c.entry
        if e.entry_id in used_entries or c.doc_id not in used_docs:
            continue
        first = used_docs[c.doc_id]
        same_ref = norm_ref(e.reference) in ("", norm_ref(first.entry.reference),
                                             norm_ref(docs[c.doc_id].invoice_number))
        if c.amount_ok and first.amount_ok and same_ref and abs(e.amount - first.entry.amount) < 0.011:
            used_entries.add(e.entry_id)
            later = max((first.entry, e), key=lambda x: (x.posting_date, x.entry_id))
            earlier = first.entry if later is e else e
            if later is first.entry:        # keep the earlier posting as the primary link
                used_docs[c.doc_id] = c
            links.append(Link(doc_id=c.doc_id, entry_id=later.entry_id, status=Status.DUPLICATE_POSTING,
                              score=c.score, reason=f"second posting of this invoice (first: {earlier.entry_id})"))

    for doc_id, c in used_docs.items():
        f = docs[doc_id]
        if not c.amount_ok:
            status, why = Status.AMOUNT_MISMATCH, f"ledger {c.entry.amount:,.2f} vs invoice {f.total:,.2f}"
            if c.amount_variant:
                why += f" — {c.amount_variant}"
        elif f.invoice_date and c.entry.fiscal_year != fiscal_year(f.invoice_date):
            status = Status.PERIOD_MISMATCH
            why = f"invoice dated {f.invoice_date} posted in FY{c.entry.fiscal_year}"
        else:
            status, why = Status.MATCHED, "; ".join(c.reasons)
        links.append(Link(doc_id=doc_id, entry_id=c.entry.entry_id, status=status, score=c.score, reason=why))

    linked_docs = {l.doc_id for l in links}
    return MatchResult(
        links=sorted(links, key=lambda l: (l.doc_id, l.entry_id)),
        unsupported_entries=sorted(e.entry_id for e in ledger if e.entry_id not in used_entries),
        unrecorded_docs=sorted(d for d in docs if d not in linked_docs),
        needs_review=needs_review,
    )
