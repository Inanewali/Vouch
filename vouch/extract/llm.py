"""LLM extractor using the Anthropic API with a forced tool call, so the model must
return fields that validate against InvoiceFields.

    ANTHROPIC_API_KEY=... python -m vouch.evaluate --extractor llm --model claude-haiku-4-5

Token counts are always recorded. Cost is computed from PRICES_PER_MTOK; add a row
(or pass --price-in/--price-out) for models not listed, after checking current
pricing at https://docs.claude.com.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import pdfplumber

from vouch.extract import register
from vouch.schemas import Extraction, InvoiceFields

# USD per million tokens (input, output). Verify against current published pricing.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

SYSTEM = (
    "You extract fields from supplier invoices for an accounts-payable audit. "
    "Return exactly what the document shows; do not guess. If a field is not on the "
    "document, return null for it. The total is the amount of THIS invoice including tax: "
    "never include a previous balance, amount carried forward, or payments. Read dates "
    "using any format hint on the document (for example DD/MM/YYYY). Set confidence below "
    "0.7 if any field was ambiguous, unreadable, or inferred."
)

TOOL = {
    "name": "record_invoice",
    "description": "Record the fields read from one invoice.",
    "input_schema": {
        "type": "object",
        "properties": {
            **InvoiceFields.model_json_schema()["properties"],
            "confidence": {"type": "number", "minimum": 0, "maximum": 1,
                           "description": "How sure you are that every field is correct"},
            "issues": {"type": "string", "description": "Anything ambiguous, briefly"},
        },
        "required": ["vendor_name", "invoice_number", "invoice_date", "total", "confidence"],
    },
}


class LLMExtractor:
    def __init__(self, model: str = "claude-haiku-4-5", mode: str = "text",
                 price_in: float | None = None, price_out: float | None = None, client=None):
        if mode not in ("text", "pdf"):
            raise ValueError("mode must be 'text' or 'pdf'")
        self.model, self.mode = model, mode
        self.name = f"llm:{model}:{mode}"
        self.prices = (price_in, price_out) if price_in is not None else PRICES_PER_MTOK.get(model)
        if client is None:
            import anthropic  # imported lazily so the rules baseline runs without the SDK/key
            client = anthropic.Anthropic()
        self.client = client

    def _content(self, pdf_path: Path) -> list[dict]:
        if self.mode == "pdf":
            data = base64.standard_b64encode(pdf_path.read_bytes()).decode()
            return [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                    "data": data}},
                    {"type": "text", "text": "Extract the invoice fields."}]
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n\n".join(p.extract_text(layout=True) or "" for p in pdf.pages)
        return [{"type": "text", "text": f"<invoice>\n{text}\n</invoice>\nExtract the invoice fields."}]

    def extract(self, doc_id: str, pdf_path: Path) -> Extraction:
        t0 = time.perf_counter()
        msg = self.client.messages.create(
            model=self.model, max_tokens=600, system=SYSTEM, tools=[TOOL],
            tool_choice={"type": "tool", "name": "record_invoice"},
            messages=[{"role": "user", "content": self._content(pdf_path)}],
        )
        block = next(b for b in msg.content if getattr(b, "type", "") == "tool_use")
        data = dict(block.input)
        conf = float(data.pop("confidence", 0.5))
        issues = data.pop("issues", None)
        fields = InvoiceFields.model_validate(data)
        cost = 0.0
        if self.prices:
            cost = (msg.usage.input_tokens * self.prices[0] + msg.usage.output_tokens * self.prices[1]) / 1e6
        return Extraction(doc_id=doc_id, fields=fields, confidence=conf, cost_usd=cost,
                          seconds=time.perf_counter() - t0, notes=issues)


@register("llm")
def _factory(**kwargs) -> LLMExtractor:
    return LLMExtractor(**kwargs)
