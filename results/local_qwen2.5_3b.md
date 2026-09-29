## local:qwen2.5:3b on 300 invoices / 298 ledger entries

| Field | Accuracy |
|---|---|
| vendor_name | 100.0% |
| invoice_number | 100.0% |
| invoice_date | 100.0% |
| subtotal | 97.7% |
| tax | 100.0% |
| total | 91.7% |
| currency | 100.0% |
| **all fields correct** | **90.3%** |

| Matching | Precision | Recall | F1 | Oracle F1 |
|---|---|---|---|---|
| document ↔ entry links | 100.0% | 97.9% | 0.990 | 1.000 |
| amount mismatch (n=13) | 41.9% | 100.0% | 0.591 | 1.000 |
| period mismatch (n=10) | 100.0% | 80.0% | 0.889 | 1.000 |
| duplicate posting (n=5) | 100.0% | 100.0% | 1.000 | 1.000 |
| unsupported entry (n=9) | 60.0% | 100.0% | 0.750 | 1.000 |
| unrecorded invoice (n=16) | 72.7% | 100.0% | 0.842 | 1.000 |
| **all exceptions** | **63.0%** | **96.2%** | **0.761** | 1.000 |

Cost: $0.00000 per document · 16726 ms per document
