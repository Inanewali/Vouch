# Vouch

Vouch matches supplier invoices to accounts-payable ledger entries and flags what an
auditor would need to follow up on. Every claim it makes is measured against a labeled
benchmark.

*Vouching* is the audit procedure of tracing a ledger entry back to its source document.
Auditors do it by hand, thousands of times per engagement. Vouch reads the invoice PDFs,
pairs them with ledger entries, and sorts out the exceptions:

| Exception | What it means |
|---|---|
| **amount mismatch** | The ledger amount differs from the invoice (transposed digits, tax left off). |
| **period mismatch** | Posted in a different fiscal year from the invoice date (a cutoff error). |
| **duplicate posting** | The same invoice was posted twice. |
| **unrecorded invoice** | An invoice that never made it into the ledger (a possible unrecorded liability). |
| **unsupported entry** | A ledger entry with no invoice behind it. |

## Results

This is the current baseline on the 300-invoice benchmark (`--n 300 --seed 7`). The
**oracle** column feeds the matcher the true invoice fields. The gap between the two
columns is error caused by extraction; anything below 1.000 in the oracle column is
matcher error.

| Extractor | All fields correct | Link F1 | Exception precision | Exception recall | Oracle exception F1 | Cost / doc |
|---|---|---|---|---|---|---|
| rules (pdfplumber + regex) | 86.3% | 0.982 | 51.5% | 94.3% | 1.000 | $0 |
| local model (Qwen2.5 3B via Ollama) | *running* | | | | | $0 |
| LLM (Claude Haiku, text mode) | *run it: see below* | | | | | |

**What the baseline gets wrong.** All 41 of its field errors are the same trap. On
statement-style invoices it reads **Amount Due**, which includes a previous balance
carried forward, as the invoice total. Each misread becomes a false amount-mismatch
alarm, so fewer than a third of its mismatch flags are real. Auditors stop trusting a
tool that cries wolf that often. The LLM extractor is prompted to exclude carried-forward
balances, and the benchmark measures whether it actually does.

## How it works

```
invoice PDFs ──► extractor ──► InvoiceFields ──┐
                (rules | local | API)   (pydantic)      ├──► matcher ──► links + exceptions ──► evaluation
ledger.csv ─────────────────────────────────────┘   (scored,                           (vs labels.json)
                                                      explainable)
```

- **Extractors** (`vouch/extract/`) return the same pydantic schema, so they are
  interchangeable. The LLM extractor forces a tool call, so its output always validates
  against the schema. It records token usage, cost, and the model's own confidence.
  Results are cached, so re-scoring never pays for the same call twice.
- **Matcher** (`vouch/match.py`) scores each invoice–entry pair on reference, vendor
  (fuzzy, ignoring suffixes like Ltd/Inc), amount (including net-of-tax and
  digit-transposition variants), and date gap. Pairs are assigned greedily by score, and
  a second pass finds duplicate postings. Every link carries a plain-language reason,
  such as "reference matches; vendor matches; amount matches; posted +6 days from invoice".
- **Benchmark generator** (`vouch/synth/`) renders invoices in three deliberately
  different layouts: letterhead with "TOTAL", statement with "Amount Due" and a previous
  balance, and a compact layout with DD/MM/YYYY dates. It then builds an AP ledger with
  the exceptions above planted, plus hard cases that should still match: blank or
  mistyped references, vendor names printed differently from the ledger, and reformatted
  invoice numbers.

## Run it

Needs Python 3.9 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt       # or: pip install -e ".[dev]"

python -m vouch.synth.generate --n 300 --seed 7 --out data/bench
python -m vouch.evaluate --bench data/bench --extractor rules

# Local open-source model: free, and documents never leave the machine
ollama pull qwen2.5:3b          # https://ollama.com
python -m vouch.evaluate --bench data/bench --extractor local --model qwen2.5:3b

# Claude API (needs ANTHROPIC_API_KEY)
python -m vouch.evaluate --bench data/bench --extractor llm --model claude-haiku-4-5 --mode text
python -m vouch.evaluate --bench data/bench --extractor llm --model claude-haiku-4-5 --mode pdf
```

Reports are written to `results/` as Markdown and JSON. The JSON includes every field
error, missed exception, and false alarm, so you can see exactly what went wrong. For a
model not listed in `vouch/extract/llm.py`, pass `--price-in` and `--price-out` (USD per
million tokens) to get costs.

```bash
pytest -q        # 19 tests; model calls are faked, so no key or Ollama needed
```

**Without installing anything:** the repo's GitHub Actions can run the benchmarks for
you. *local-model-benchmark* installs Ollama on GitHub's runner and is free; *llm-benchmark*
calls the Claude API and needs a repository secret named `ANTHROPIC_API_KEY`. Both commit
their results to `results/`.

## Data

Every document is synthetic, generated from fictional vendors and marked as such on the
page. Don't add real client invoices to this repository.

## Roadmap

1. **Make the benchmark harder.** With the true fields, the matcher currently scores
   1.000, which means the benchmark doesn't yet stress matching. Next: scanned and skewed
   images (OCR noise), credit notes, partial payments, multi-currency, multi-page
   invoices, and vendors sharing reference formats.
2. **Model comparison.** Rules vs a small model vs a frontier model on accuracy, cost per
   document, and latency. Then a cascade: rules first, with the LLM only for documents
   the rules can't read confidently.
3. **Human review queue.** Route low-confidence extractions to review instead of letting
   them drive matching, and measure how many errors that catches per item reviewed.
4. **Pipeline and dashboard.** Land results in DuckDB, transform them with dbt, and show
   match rate, the exception queue, and processing cost in a dashboard.
