from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from vouch.evaluate import evaluate, field_ok, prf
from vouch.extract.llm import LLMExtractor
from vouch.extract.rules import RulesExtractor
from vouch.match import is_transposition, match, norm_ref, score, vendor_sim
from vouch.schemas import Extraction, InvoiceFields, LedgerEntry, Status
from vouch.synth.generate import generate, transposed


@pytest.fixture(scope="module")
def bench(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("bench")
    generate(60, seed=3, out=out, rates={"amount_mismatch": .1, "period_mismatch": .1,
                                         "duplicate_posting": .08, "unrecorded_invoice": .08,
                                         "unsupported_entry": .08})
    return out


# ---- generator ------------------------------------------------------------------
def test_generator_is_deterministic(tmp_path):
    a = generate(20, 11, tmp_path / "a")
    b = generate(20, 11, tmp_path / "b")
    assert a.model_dump() == b.model_dump()
    assert (tmp_path / "a" / "ledger.csv").read_text() == (tmp_path / "b" / "ledger.csv").read_text()


def test_every_exception_type_is_planted(bench):
    import json
    labels = json.loads((bench / "labels.json").read_text())
    assert all(labels["exceptions"][k] for k in labels["exceptions"]), labels["exceptions"]


def test_transposed_swaps_adjacent_digits():
    import random
    rng = random.Random(0)
    for amt in [1254.00, 98765.43, 31.10]:
        t = transposed(amt, rng)
        assert t != amt and sorted(f"{t:.2f}") == sorted(f"{amt:.2f}")


# ---- matching helpers -----------------------------------------------------------
def test_reference_and_vendor_normalisation():
    assert norm_ref(" nw-05419") == norm_ref("NW 05419") == "NW05419"
    assert vendor_sim("NORTHWIND LOGISTICS LIMITED", "Northwind Logistics Ltd") == 100
    assert vendor_sim("Prairie Tool and Die Ltd.", "Prairie Tool & Die") == 100
    assert vendor_sim("Harbor Steel Supply", "Granite Legal LLP") < 60


def test_is_transposition():
    assert is_transposition(1254.00, 1524.00)
    assert not is_transposition(1254.00, 1254.00)
    assert not is_transposition(1254.00, 1255.00)


def entry(eid, amount, ref="INV-1", vendor="Acme Ltd", d=date(2025, 3, 5)):
    return LedgerEntry(entry_id=eid, posting_date=d, fiscal_year=d.year, vendor_name=vendor,
                       reference=ref, amount=amount)


def fields(total=100.0, number="INV-1", vendor="ACME LIMITED", d=date(2025, 3, 1), sub=None, tax=None):
    return InvoiceFields(vendor_name=vendor, invoice_number=number, invoice_date=d,
                         total=total, subtotal=sub, tax=tax, currency="CAD")


def ex(f, doc="D1"):
    return {doc: Extraction(doc_id=doc, fields=f)}


def test_neighbouring_invoice_number_alone_is_not_a_match():
    # INV-2 vs INV-1 is one character off but the amount is unrelated
    assert score("D1", fields(number="INV-2"), entry("E1", 5555.0)) is None


def test_blank_reference_matches_on_vendor_amount_date():
    m = match(ex(fields()), [entry("E1", 100.0, ref="")])
    assert [(l.entry_id, l.status) for l in m.links] == [("E1", Status.MATCHED)]


def test_amount_mismatch_net_of_tax():
    m = match(ex(fields(total=105.0, sub=100.0, tax=5.0)), [entry("E1", 100.0)])
    assert m.links[0].status == Status.AMOUNT_MISMATCH
    assert "net of tax" in m.links[0].reason


def test_period_mismatch():
    m = match(ex(fields(d=date(2025, 12, 20))), [entry("E1", 100.0, d=date(2026, 1, 8))])
    assert m.links[0].status == Status.PERIOD_MISMATCH


def test_duplicate_posting_flags_the_later_entry():
    led = [entry("E2", 100.0, d=date(2025, 3, 30), ref=""), entry("E1", 100.0)]
    m = match(ex(fields()), led)
    st = {l.entry_id: l.status for l in m.links}
    assert st == {"E1": Status.MATCHED, "E2": Status.DUPLICATE_POSTING}


def test_unsupported_and_unrecorded():
    m = match(ex(fields(number="X-9", vendor="Other Co", total=42.0)), [entry("E1", 100.0)])
    assert m.unsupported_entries == ["E1"] and m.unrecorded_docs == ["D1"]


# ---- evaluation ---------------------------------------------------------------
def test_prf():
    r = prf({"a", "b", "c"}, {"a", "b", "d", "e"})
    assert r["precision"] == pytest.approx(2 / 3) and r["recall"] == 0.5


def test_field_ok_tolerances():
    assert field_ok("total", 100.004, 100.0)
    assert field_ok("invoice_number", "nw 05419", "NW-05419")
    assert not field_ok("invoice_date", date(2025, 5, 6), date(2025, 6, 5))


def test_oracle_matching_is_perfect_and_rules_run(bench):
    r = evaluate(bench, "rules")
    assert r["matching_oracle"]["links"]["f1"] == 1.0
    assert r["matching_oracle"]["exceptions_overall"]["f1"] == 1.0
    fa = r["extraction"]["field_accuracy"]
    assert fa["invoice_number"] == 1.0 and fa["invoice_date"] == 1.0
    assert r["matching"]["links"]["precision"] > 0.95


# ---- extractors -----------------------------------------------------------------
def test_rules_extractor_reads_each_layout(bench):
    import json
    truth = json.loads((bench / "labels.json").read_text())["fields"]
    rx = RulesExtractor()
    for doc in ["DOC0001", "DOC0002", "DOC0003"]:
        got = rx.extract(doc, bench / "docs" / f"{doc}.pdf").fields
        assert str(got.invoice_date) == truth[doc]["invoice_date"]
        assert norm_ref(got.invoice_number) == norm_ref(truth[doc]["invoice_number"])


class FakeClient:
    """Stands in for anthropic.Anthropic so the LLM path is tested without a key."""
    def __init__(self, payload):
        self.payload, self.calls = payload, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=dict(self.payload))],
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=150))


def test_llm_extractor_parses_tool_output_and_costs(bench):
    payload = {"vendor_name": "Harbor Steel Supply", "invoice_number": "HSS-01234",
               "invoice_date": "2025-04-02", "subtotal": 100.0, "tax": 5.0, "total": 105.0,
               "currency": "CAD", "confidence": 0.93, "issues": "previous balance ignored"}
    fake = FakeClient(payload)
    llm = LLMExtractor(model="claude-haiku-4-5", client=fake)
    e = llm.extract("DOC0001", bench / "docs" / "DOC0001.pdf")
    assert e.fields.total == 105.0 and e.fields.invoice_date == date(2025, 4, 2)
    assert e.confidence == 0.93 and e.notes == "previous balance ignored"
    assert e.cost_usd == pytest.approx((1200 * 1.0 + 150 * 5.0) / 1e6)
    call = fake.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "record_invoice"}
    assert "<invoice>" in call["messages"][0]["content"][0]["text"]


def test_llm_pdf_mode_sends_document(bench):
    fake = FakeClient({"vendor_name": "x", "invoice_number": "1", "invoice_date": None,
                       "total": 1.0, "confidence": 0.4})
    LLMExtractor(mode="pdf", client=fake).extract("DOC0001", bench / "docs" / "DOC0001.pdf")
    assert fake.calls[0]["messages"][0]["content"][0]["type"] == "document"


import json


def test_local_extractor_with_fake_ollama(bench, monkeypatch):
    from vouch.extract.local import LocalExtractor
    sent = {}

    def fake_post(self, body):
        sent.update(body)
        return {"message": {"content": json.dumps({
            "vendor_name": "Harbor Steel Supply", "invoice_number": "HSS-01234",
            "invoice_date": "2025-04-02", "subtotal": "1,000.00", "tax": 50, "total": 1050,
            "currency": "CAD", "confidence": 0.9})}}

    import json
    monkeypatch.setattr(LocalExtractor, "_post", fake_post)
    e = LocalExtractor(model="qwen2.5:3b").extract("DOC0001", bench / "docs" / "DOC0001.pdf")
    assert e.fields.subtotal == 1000.0 and e.fields.total == 1050.0
    assert e.fields.invoice_date == date(2025, 4, 2) and e.cost_usd == 0.0
    assert sent["format"]["required"] and sent["options"]["temperature"] == 0


def test_local_extractor_drops_bad_date_not_whole_doc():
    from vouch.extract.local import _fields
    f = _fields({"vendor_name": "A", "invoice_number": "1", "invoice_date": "04/03/2025", "total": 5})
    assert f.invoice_date is None and f.total == 5.0
