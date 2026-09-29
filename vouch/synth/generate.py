"""Generate a labeled benchmark: invoice PDFs, an AP ledger, and ground truth.

    python -m vouch.synth.generate --n 300 --seed 7 --out data/bench

Planted problems (the things an auditor vouching AP would need to catch):
  amount_mismatch     ledger amount differs from the invoice (transposed digits, tax left off)
  period_mismatch     posted in a different fiscal year from the invoice date (cutoff error)
  duplicate_posting   the same invoice posted twice
  unrecorded_invoice  an invoice that was never posted
  unsupported_entry   a ledger entry with no invoice behind it

Plus clean-but-awkward cases that must still match: blank or mistyped references,
vendor names printed differently from the ledger, statement-style invoices whose
"Amount Due" includes a previous balance.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from vouch.schemas import ExceptionType, GroundTruth, InvoiceFields
from vouch.synth.render import Invoice, Line, render

FY = 2025


@dataclass
class Vendor:
    ledger_name: str
    printed_names: list[str]
    address: str
    number_format: str       # python format with {n}
    layout: str
    tax: tuple[float, str]
    currency: str = "CAD"
    catalog: tuple[tuple[str, float, float], ...] = ()   # (item, min price, max price)


VENDORS = [
    Vendor("Northwind Logistics Ltd", ["Northwind Logistics Ltd.", "NORTHWIND LOGISTICS LIMITED"],
           "4410 36 St NE\nCalgary, AB T1Y 6H4", "NW-{n:05d}", "classic", (0.05, "GST"),
           catalog=(("Freight - LTL Calgary to Edmonton", 380, 1450), ("Fuel surcharge", 40, 180),
                    ("Pallet handling", 12, 35))),
    Vendor("Bluefield Office Supply", ["Bluefield Office Supply", "Bluefield Office Supply Co."],
           "118 7 Ave SW\nCalgary, AB T2P 0W5", "INV-{n:06d}", "compact", (0.05, "GST"),
           catalog=(("Copy paper, case", 38, 62), ("Toner cartridge", 85, 240), ("Desk chair", 180, 420))),
    Vendor("Cedar Mechanical Services", ["Cedar Mechanical Services Inc."],
           "9 Burnside Rd\nRed Deer, AB T4N 5V3", "CMS{n:04d}", "classic", (0.05, "GST"),
           catalog=(("HVAC service call", 145, 320), ("Compressor replacement", 1800, 5200),
                    ("Labour, hourly", 95, 135))),
    Vendor("Harbor Steel Supply", ["Harbor Steel Supply", "HARBOR STEEL"],
           "77 Industrial Way\nEdmonton, AB T6E 5Z2", "HSS-{n:05d}", "statement", (0.05, "GST"),
           catalog=(("Hot-rolled plate 3/8in", 900, 4800), ("Square tube 2x2", 220, 980),
                    ("Cutting service", 60, 240))),
    Vendor("Summit Power & Gas", ["Summit Power & Gas"],
           "PO Box 2201\nCalgary, AB T2P 2M4", "{n:010d}", "statement", (0.05, "GST"),
           catalog=(("Electricity - plant", 4200, 11800), ("Natural gas - plant", 900, 5200))),
    Vendor("Riverbend Packaging", ["Riverbend Packaging Corp.", "Riverbend Packaging"],
           "3100 Riverbend Dr\nWinnipeg, MB R3C 4T7", "RB/{n:05d}", "compact", (0.05, "GST"),
           catalog=(("Corrugated boxes 24x18x12", 1.2, 2.8), ("Stretch wrap roll", 28, 44),
                    ("Custom print setup", 150, 400))),
    Vendor("Ironwood IT Consulting", ["Ironwood IT Consulting"],
           "600 1 St SW, Suite 900\nCalgary, AB T2P 1M3", "IW-2025-{n:04d}", "classic", (0.05, "GST"),
           catalog=(("Managed IT services - monthly", 2400, 3900), ("After-hours support, hourly", 140, 190),
                    ("Firewall licence renewal", 900, 2600))),
    Vendor("Prairie Tool & Die", ["Prairie Tool & Die", "Prairie Tool and Die Ltd."],
           "15 Commerce Cres\nSaskatoon, SK S7K 1N9", "PTD{n:05d}", "compact", (0.05, "GST"),
           catalog=(("Die repair", 600, 3200), ("Tool steel blanks", 150, 900))),
    Vendor("Granite Legal LLP", ["Granite Legal LLP"],
           "250 5 St SW\nCalgary, AB T2P 0R3", "{n:06d}", "classic", (0.05, "GST"),
           catalog=(("Professional services - contract review", 1500, 7800), ("Disbursements", 40, 380))),
    Vendor("Lakeside Janitorial", ["Lakeside Janitorial", "Lakeside Janitorial Services"],
           "2 Lakeside Bay\nChestermere, AB T1X 1A1", "LJ-{n:04d}", "compact", (0.05, "GST"),
           catalog=(("Monthly cleaning contract", 1800, 2600), ("Floor stripping & wax", 350, 900))),
    Vendor("Falcon Industrial Parts (US)", ["Falcon Industrial Parts, Inc."],
           "1200 Harbor Blvd\nSeattle, WA 98101", "FIP-{n:06d}", "classic", (0.0, "Sales tax"), "USD",
           catalog=(("Bearing assembly", 120, 680), ("Hydraulic seal kit", 45, 210),
                    ("Expedited shipping", 60, 260))),
    Vendor("Evergreen Waste Solutions", ["Evergreen Waste Solutions"],
           "88 Landfill Rd\nCalgary, AB T2C 4L9", "EWS{n:05d}", "statement", (0.05, "GST"),
           catalog=(("Bin service - 40yd", 480, 920), ("Scrap metal pickup", 150, 400))),
]



def transposed(amount: float, rng: random.Random) -> float:
    """Swap two adjacent digits of the integer part — the classic keying error."""
    s = f"{amount:.2f}"
    whole, cents = s.split(".")
    if len(whole) < 2:
        return round(amount + 9.0, 2)
    for _ in range(10):
        i = rng.randrange(len(whole) - 1)
        if whole[i] != whole[i + 1] and not (i == 0 and whole[1] == "0"):  # no leading zero
            w = whole[:i] + whole[i + 1] + whole[i] + whole[i + 2:]
            return float(f"{int(w)}.{cents}")
    return round(amount + 90.0, 2)


def mistype(ref: str, rng: random.Random) -> str:
    digits = [i for i, ch in enumerate(ref) if ch.isdigit()]
    if not digits:
        return ref
    i = rng.choice(digits)
    return ref[:i] + str((int(ref[i]) + rng.randint(1, 8)) % 10) + ref[i + 1:]


def reformat(ref: str, rng: random.Random) -> str:
    return rng.choice([ref.replace("-", "").replace("/", ""), ref.lower(), " " + ref, ref.replace("-", " ")])


def generate(n: int, seed: int, out: Path, rates: dict[str, float] | None = None) -> GroundTruth:
    rates = {"amount_mismatch": 0.06, "period_mismatch": 0.04, "duplicate_posting": 0.03,
             "unrecorded_invoice": 0.04, "unsupported_entry": 0.03, **(rates or {})}
    rng = random.Random(seed)
    docs_dir = out / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    counters = {v.ledger_name: rng.randint(100, 9000) for v in VENDORS}
    fields: dict[str, InvoiceFields] = {}
    links: list[tuple[str, str]] = []
    exceptions: dict[str, list[str]] = {e.value: [] for e in ExceptionType}
    notes: dict[str, str] = {}
    ledger: list[dict] = []
    entry_seq = 0

    def post(doc_id: str | None, vendor: Vendor, posting: date, ref: str, amount: float, desc: str) -> str:
        nonlocal entry_seq
        entry_seq += 1
        eid = f"JE{entry_seq:05d}"
        ledger.append({"entry_id": eid, "posting_date": posting.isoformat(), "fiscal_year": posting.year,
                       "vendor_name": vendor.ledger_name, "reference": ref, "amount": f"{amount:.2f}",
                       "description": desc})
        return eid

    start = date(FY, 1, 3)
    for i in range(n):
        v = rng.choice(VENDORS)
        counters[v.ledger_name] += rng.randint(1, 40)
        number = v.number_format.format(n=counters[v.ledger_name])
        # mostly within FY, a few dated in January of next year (cutoff zone)
        inv_date = start + timedelta(days=rng.randint(0, 362)) if rng.random() > 0.05 \
            else date(FY + 1, 1, rng.randint(2, 20))
        lines = [Line(item, qty, round(rng.uniform(lo, hi), 2))
                 for item, lo, hi in rng.sample(v.catalog, k=rng.randint(1, len(v.catalog)))
                 for qty in [rng.choice([1, 1, 1, 2, 3, 5, 10]) if hi > 30 else rng.randint(100, 2000)]]
        inv = Invoice(doc_id=f"DOC{i + 1:04d}", vendor_name=rng.choice(v.printed_names),
                      vendor_address=v.address, invoice_number=number, invoice_date=inv_date,
                      lines=lines, tax_rate=v.tax[0], tax_label=v.tax[1], currency=v.currency,
                      layout=v.layout,
                      previous_balance=round(rng.uniform(200, 9000), 2)
                      if v.layout == "statement" and rng.random() < 0.6 else 0.0,
                      po_number=f"PO-{rng.randint(10000, 99999)}" if rng.random() < 0.4 else None)
        render(inv, docs_dir / f"{inv.doc_id}.pdf")
        fields[inv.doc_id] = InvoiceFields(vendor_name=inv.vendor_name, invoice_number=number,
                                           invoice_date=inv_date, subtotal=inv.subtotal, tax=inv.tax,
                                           total=inv.total, currency=inv.currency)

        # ---- how it was posted ------------------------------------------------
        r = rng.random()
        if r < rates["unrecorded_invoice"]:
            exceptions["unrecorded_invoice"].append(inv.doc_id)
            notes[inv.doc_id] = "never posted"
            continue

        lag = rng.randint(0, 20)
        posting = inv_date + timedelta(days=lag)
        if posting.year != inv_date.year:           # correct cutoff: accrue in the invoice's year
            posting = date(inv_date.year, 12, 31)
        ref = number
        rr = rng.random()
        if rr < 0.15:
            ref = ""
        elif rr < 0.25:
            ref = reformat(number, rng)
        elif rr < 0.30:
            ref = mistype(number, rng)
        amount = inv.total
        desc = f"{v.ledger_name} - {lines[0].description}"[:60]

        kind = rng.random()
        r1 = rates["amount_mismatch"]
        r2 = r1 + rates["period_mismatch"]
        r3 = r2 + rates["duplicate_posting"]
        if kind < r1:
            if inv.tax > 0 and rng.random() < 0.4:
                amount = inv.subtotal
                notes[inv.doc_id] = "posted net of tax"
            else:
                amount = transposed(inv.total, rng)
                notes[inv.doc_id] = f"transposed {inv.total:.2f} -> {amount:.2f}"
            eid = post(inv.doc_id, v, posting, ref, amount, desc)
            exceptions["amount_mismatch"].append(f"{inv.doc_id}|{eid}")
        elif kind < r2:
            if inv_date.month == 12 or inv_date.year > FY:
                posting = date(inv_date.year + 1, 1, rng.randint(2, 25)) if inv_date.year == FY \
                    else date(FY, 12, rng.randint(20, 31))
            else:
                posting = date(inv_date.year - 1, 12, rng.randint(15, 31))
            eid = post(inv.doc_id, v, posting, ref, amount, desc)
            exceptions["period_mismatch"].append(f"{inv.doc_id}|{eid}")
            notes[inv.doc_id] = f"invoice {inv_date} posted {posting}"
        elif kind < r3:
            post(inv.doc_id, v, posting, ref, amount, desc)
            dup_ref = rng.choice([ref, reformat(number, rng), ""])
            eid2 = post(inv.doc_id, v, posting + timedelta(days=rng.randint(3, 40)), dup_ref, amount, desc)
            links.append((inv.doc_id, ledger[-2]["entry_id"]))
            exceptions["duplicate_posting"].append(eid2)
            notes[eid2] = f"second posting of {inv.doc_id}"
            eid = eid2
        else:
            eid = post(inv.doc_id, v, posting, ref, amount, desc)
        links.append((inv.doc_id, eid))

    # ledger entries with no invoice at all
    n_unsupported = max(1, round(n * rates["unsupported_entry"]))
    for _ in range(n_unsupported):
        v = rng.choice(VENDORS)
        counters[v.ledger_name] += rng.randint(1, 40)
        fake = v.number_format.format(n=counters[v.ledger_name]) if rng.random() < 0.7 else ""
        amount = round(rng.uniform(250, 9500), 2)
        eid = post(None, v, start + timedelta(days=rng.randint(0, 362)), fake, amount,
                   f"{v.ledger_name} - {v.catalog[0][0]}"[:60])
        exceptions["unsupported_entry"].append(eid)
        notes[eid] = "no supporting invoice"

    rng.shuffle(ledger)
    ledger.sort(key=lambda e: e["posting_date"])
    with open(out / "ledger.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ledger[0]))
        w.writeheader()
        w.writerows(ledger)

    truth = GroundTruth(fields=fields, links=links, exceptions=exceptions, seed=seed, notes=notes)
    (out / "labels.json").write_text(truth.model_dump_json(indent=1))
    return truth


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=Path("data/bench"))
    a = ap.parse_args()
    t = generate(a.n, a.seed, a.out)
    counts = {k: len(v) for k, v in t.exceptions.items()}
    print(f"{len(t.fields)} invoices, {len(t.links)} true links -> {a.out}\nplanted: {json.dumps(counts)}")


if __name__ == "__main__":
    main()
