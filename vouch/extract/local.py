"""Local-model extractor using Ollama (https://ollama.com). Free, and no document
leaves the machine, which matters when the invoices belong to an audit client.

    ollama pull qwen2.5:3b
    python -m vouch.evaluate --extractor local --model qwen2.5:3b

Uses Ollama's structured outputs (a JSON schema in `format`), so the reply always
parses into InvoiceFields. Only the Python standard library is needed to call it.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

import pdfplumber

from vouch.extract import register
from vouch.extract.llm import SYSTEM
from vouch.schemas import Extraction, InvoiceFields

SCHEMA = {
    "type": "object",
    "properties": {
        "vendor_name": {"type": ["string", "null"]},
        "invoice_number": {"type": ["string", "null"]},
        "invoice_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "subtotal": {"type": ["number", "null"]},
        "tax": {"type": ["number", "null"]},
        "total": {"type": ["number", "null"]},
        "currency": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": ["vendor_name", "invoice_number", "invoice_date", "subtotal", "tax", "total",
                 "currency", "confidence"],
}


def _fields(data: dict) -> InvoiceFields:
    """Validate, dropping any single field the model returned in an unusable form
    rather than losing the whole document."""
    from datetime import date
    d = data.get("invoice_date")
    if isinstance(d, str):
        try:
            data["invoice_date"] = date.fromisoformat(d.strip()[:10])
        except ValueError:
            data["invoice_date"] = None
    for k in ("subtotal", "tax", "total"):
        v = data.get(k)
        if isinstance(v, str):
            try:
                data[k] = float(v.replace(",", "").replace("$", ""))
            except ValueError:
                data[k] = None
    return InvoiceFields.model_validate({k: data.get(k) for k in InvoiceFields.model_fields})


class LocalExtractor:
    def __init__(self, model: str = "qwen2.5:3b", host: str | None = None, timeout: float = 300):
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.timeout = timeout
        self.name = f"local:{model}"

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(f"{self.host}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def extract(self, doc_id: str, pdf_path: Path) -> Extraction:
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n\n".join(p.extract_text(layout=True) or "" for p in pdf.pages)
        t0 = time.perf_counter()
        out = self._post({
            "model": self.model,
            "stream": False,
            "format": SCHEMA,
            "options": {"temperature": 0, "num_ctx": 4096},
            "messages": [
                {"role": "system", "content": SYSTEM + " Reply with JSON only. Dates as YYYY-MM-DD."},
                {"role": "user", "content": f"<invoice>\n{text}\n</invoice>\nExtract the invoice fields."},
            ],
        })
        data = json.loads(out["message"]["content"])
        conf = float(data.pop("confidence", 0.5) or 0.5)
        fields = _fields(data)
        return Extraction(doc_id=doc_id, fields=fields, confidence=conf, cost_usd=0.0,
                          seconds=time.perf_counter() - t0)


@register("local")
def _factory(**kwargs) -> LocalExtractor:
    kwargs.pop("mode", None)
    kwargs.pop("price_in", None)
    kwargs.pop("price_out", None)
    return LocalExtractor(**kwargs)
