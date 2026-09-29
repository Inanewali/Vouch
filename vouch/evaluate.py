"""Score an extractor + the matcher against a labeled benchmark.

    python -m vouch.evaluate --bench data/bench --extractor rules
    python -m vouch.evaluate --bench data/bench --extractor llm --model claude-haiku-4-5

Every run also scores an "oracle" pass that feeds the matcher the true invoice fields.
The gap between oracle and extractor is the error caused by extraction; the oracle's
own misses are matcher errors. Keeping them apart shows where to spend effort.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from vouch import extract
from vouch.match import load_ledger, match, norm_ref, norm_vendor
from vouch.schemas import Extraction, GroundTruth, InvoiceFields, MatchResult, Status

FIELDS = ["vendor_name", "invoice_number", "invoice_date", "subtotal", "tax", "total", "currency"]


def field_ok(name: str, pred, true) -> bool:
    if pred is None or true is None:
        return pred is None and true is None
    if name in ("subtotal", "tax", "total"):
        return abs(float(pred) - float(true)) < 0.011
    if name == "vendor_name":
        return norm_vendor(pred) == norm_vendor(true)
    if name == "invoice_number":
        return norm_ref(pred) == norm_ref(true)
    return pred == true


def extraction_metrics(ex: dict[str, Extraction], truth: GroundTruth) -> dict:
    per = {f: 0 for f in FIELDS}
    all_ok = 0
    errors: list[dict] = []
    for doc_id, t in truth.fields.items():
        p = ex[doc_id].fields
        ok_all = True
        for f in FIELDS:
            ok = field_ok(f, getattr(p, f), getattr(t, f))
            per[f] += ok
            if not ok:
                ok_all = False
                errors.append({"doc_id": doc_id, "field": f, "pred": str(getattr(p, f)),
                               "true": str(getattr(t, f))})
        all_ok += ok_all
    n = len(truth.fields)
    return {"field_accuracy": {f: per[f] / n for f in FIELDS}, "doc_all_fields_correct": all_ok / n,
            "errors": errors}


def prf(pred: set, true: set) -> dict:
    tp = len(pred & true)
    p = tp / len(pred) if pred else (1.0 if not true else 0.0)
    r = tp / len(true) if true else 1.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": len(pred - true), "fn": len(true - pred),
            "missed": sorted(true - pred)[:20], "false_alarms": sorted(pred - true)[:20]}


def predicted_exceptions(m: MatchResult) -> dict[str, set[str]]:
    out = {"amount_mismatch": set(), "period_mismatch": set(), "duplicate_posting": set(),
           "unsupported_entry": set(m.unsupported_entries), "unrecorded_invoice": set(m.unrecorded_docs)}
    for l in m.links:
        if l.status == Status.AMOUNT_MISMATCH:
            out["amount_mismatch"].add(f"{l.doc_id}|{l.entry_id}")
        elif l.status == Status.PERIOD_MISMATCH:
            out["period_mismatch"].add(f"{l.doc_id}|{l.entry_id}")
        elif l.status == Status.DUPLICATE_POSTING:
            out["duplicate_posting"].add(l.entry_id)
    return out


def matching_metrics(m: MatchResult, truth: GroundTruth) -> dict:
    pred_links = {(l.doc_id, l.entry_id) for l in m.links}
    true_links = {tuple(x) for x in truth.links}
    pe = predicted_exceptions(m)
    exc = {k: prf(pe[k], set(v)) for k, v in truth.exceptions.items()}
    all_pred = set().union(*[{f"{k}:{x}" for x in v} for k, v in pe.items()])
    all_true = set().union(*[{f"{k}:{x}" for x in v} for k, v in truth.exceptions.items()])
    return {"links": prf({f"{a}|{b}" for a, b in pred_links}, {f"{a}|{b}" for a, b in true_links}),
            "exceptions": exc, "exceptions_overall": prf(all_pred, all_true)}


def oracle(truth: GroundTruth) -> dict[str, Extraction]:
    return {k: Extraction(doc_id=k, fields=InvoiceFields.model_validate(v.model_dump()))
            for k, v in truth.fields.items()}


def evaluate(bench: Path, extractor_name: str, **kw) -> dict:
    truth = GroundTruth.model_validate_json((bench / "labels.json").read_text())
    ledger = load_ledger(bench / "ledger.csv")
    ex = extract.get(extractor_name, **kw)
    cache = bench / "extractions" / f"{ex.name.replace(':', '_')}.jsonl"
    results = extract.run(ex, bench / "docs", cache=cache)

    m = match(results, ledger)
    mo = match(oracle(truth), ledger)
    n = len(results)
    report = {
        "extractor": ex.name, "bench": str(bench), "documents": n, "ledger_entries": len(ledger),
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "cost_usd_total": sum(e.cost_usd for e in results.values()),
        "cost_usd_per_doc": sum(e.cost_usd for e in results.values()) / n if n else 0,
        "seconds_per_doc": sum(e.seconds for e in results.values()) / n if n else 0,
        "extraction": extraction_metrics(results, truth),
        "matching": matching_metrics(m, truth),
        "matching_oracle": matching_metrics(mo, truth),
    }
    return report


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def to_markdown(r: dict) -> str:
    e, mm, mo = r["extraction"], r["matching"], r["matching_oracle"]
    out = [f"## {r['extractor']} on {r['documents']} invoices / {r['ledger_entries']} ledger entries", ""]
    out += ["| Field | Accuracy |", "|---|---|"]
    out += [f"| {k} | {pct(v)} |" for k, v in e["field_accuracy"].items()]
    out += [f"| **all fields correct** | **{pct(e['doc_all_fields_correct'])}** |", ""]
    out += ["| Matching | Precision | Recall | F1 | Oracle F1 |", "|---|---|---|---|---|"]
    out.append(f"| document ↔ entry links | {pct(mm['links']['precision'])} | {pct(mm['links']['recall'])} | "
               f"{mm['links']['f1']:.3f} | {mo['links']['f1']:.3f} |")
    for k, v in mm["exceptions"].items():
        n_true = v["tp"] + v["fn"]
        out.append(f"| {k.replace('_', ' ')} (n={n_true}) | {pct(v['precision'])} | {pct(v['recall'])} | "
                   f"{v['f1']:.3f} | {mo['exceptions'][k]['f1']:.3f} |")
    o = mm["exceptions_overall"]
    out.append(f"| **all exceptions** | **{pct(o['precision'])}** | **{pct(o['recall'])}** | "
               f"**{o['f1']:.3f}** | {mo['exceptions_overall']['f1']:.3f} |")
    out += ["", f"Cost: ${r['cost_usd_per_doc']:.5f} per document · {r['seconds_per_doc'] * 1000:.0f} ms per document"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench", type=Path, default=Path("data/bench"))
    ap.add_argument("--extractor", default="rules")
    ap.add_argument("--model", help="LLM model id (llm extractor)")
    ap.add_argument("--mode", choices=["text", "pdf"], help="send extracted text or the PDF itself (llm)")
    ap.add_argument("--price-in", type=float, help="USD per million input tokens (llm)")
    ap.add_argument("--price-out", type=float, help="USD per million output tokens (llm)")
    ap.add_argument("--out", type=Path, default=Path("results"))
    a = ap.parse_args()
    kw = {k: v for k, v in {"model": a.model, "mode": a.mode, "price_in": a.price_in,
                            "price_out": a.price_out}.items() if v is not None}
    r = evaluate(a.bench, a.extractor, **kw)
    a.out.mkdir(parents=True, exist_ok=True)
    stem = r["extractor"].replace(":", "_")
    (a.out / f"{stem}.json").write_text(json.dumps(r, indent=1, default=str))
    md = to_markdown(r)
    (a.out / f"{stem}.md").write_text(md + "\n")
    print(md)


if __name__ == "__main__":
    main()
