## local:qwen2.5:3b on 20 invoices / 21 ledger entries

| Field | Accuracy |
|---|---|
| vendor_name | 100.0% |
| invoice_number | 95.0% |
| invoice_date | 100.0% |
| subtotal | 100.0% |
| tax | 100.0% |
| total | 90.0% |
| currency | 100.0% |
| **all fields correct** | **85.0%** |

| Matching | Precision | Recall | F1 | Oracle F1 |
|---|---|---|---|---|
| document ↔ entry links | 100.0% | 100.0% | 1.000 | 1.000 |
| amount mismatch (n=1) | 33.3% | 100.0% | 0.500 | 1.000 |
| period mismatch (n=1) | 100.0% | 100.0% | 1.000 | 1.000 |
| duplicate posting (n=0) | 100.0% | 100.0% | 1.000 | 1.000 |
| unsupported entry (n=1) | 100.0% | 100.0% | 1.000 | 1.000 |
| unrecorded invoice (n=0) | 100.0% | 100.0% | 1.000 | 1.000 |
| **all exceptions** | **60.0%** | **100.0%** | **0.750** | 1.000 |

Cost: $0.00000 per document · 13638 ms per document
