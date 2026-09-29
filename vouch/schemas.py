"""Shared data models.

`InvoiceFields` is what an extractor must return for one document. The same model
is used for ground truth, so extraction accuracy is a field-by-field comparison.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class InvoiceFields(BaseModel):
    vendor_name: str | None = Field(None, description="Name of the business that issued the invoice")
    invoice_number: str | None = Field(None, description="The invoice's own identifier, as printed")
    invoice_date: date | None = Field(None, description="Date the invoice was issued")
    subtotal: float | None = Field(None, description="Amount before tax")
    tax: float | None = Field(None, description="Total sales tax (GST/HST/PST) on this invoice")
    total: float | None = Field(
        None, description="Amount of THIS invoice including tax. Exclude any previous or "
                          "outstanding balance carried forward.")
    currency: str | None = Field(None, description="ISO currency code, e.g. CAD or USD")


class Extraction(BaseModel):
    doc_id: str
    fields: InvoiceFields
    confidence: float = 1.0          # extractor's own confidence, 0..1
    cost_usd: float = 0.0            # API cost for this document, if any
    seconds: float = 0.0
    error: str | None = None
    notes: str | None = None         # extractor's own remarks (e.g. what was ambiguous)


class LedgerEntry(BaseModel):
    entry_id: str
    posting_date: date
    fiscal_year: int
    vendor_name: str
    reference: str                   # invoice number as keyed by the clerk; may be blank or mistyped
    amount: float
    description: str = ""


class Status(str, Enum):
    MATCHED = "matched"
    AMOUNT_MISMATCH = "amount_mismatch"
    PERIOD_MISMATCH = "period_mismatch"
    DUPLICATE_POSTING = "duplicate_posting"


class ExceptionType(str, Enum):
    AMOUNT_MISMATCH = "amount_mismatch"
    PERIOD_MISMATCH = "period_mismatch"
    DUPLICATE_POSTING = "duplicate_posting"
    UNSUPPORTED_ENTRY = "unsupported_entry"      # ledger entry with no document behind it
    UNRECORDED_INVOICE = "unrecorded_invoice"    # document that was never posted


class Link(BaseModel):
    doc_id: str
    entry_id: str
    status: Status
    score: float = 1.0
    reason: str = ""


class MatchResult(BaseModel):
    links: list[Link]
    unsupported_entries: list[str]
    unrecorded_docs: list[str]
    needs_review: list[str] = []     # doc_ids whose extraction was too uncertain to trust


class GroundTruth(BaseModel):
    """Labels for a generated benchmark."""
    fields: dict[str, InvoiceFields]                 # doc_id -> true fields
    links: list[tuple[str, str]]                     # true (doc_id, entry_id) pairs
    exceptions: dict[str, list[str]]                 # ExceptionType value -> item keys
    seed: int
    notes: dict[str, str] = {}                       # doc_id or entry_id -> what was planted
