## rules on 300 invoices / 298 ledger entries

| Field | Accuracy |
|---|---|
| vendor_name | 100.0% |
| invoice_number | 100.0% |
| invoice_date | 100.0% |
| subtotal | 100.0% |
| tax | 100.0% |
| total | 86.3% |
| currency | 100.0% |
| **all fields correct** | **86.3%** |

| Matching | Precision | Recall | F1 | Oracle F1 |
|---|---|---|---|---|
| document ↔ entry links | 100.0% | 96.5% | 0.982 | 1.000 |
| amount mismatch (n=13) | 31.7% | 100.0% | 0.481 | 1.000 |
| period mismatch (n=10) | 100.0% | 80.0% | 0.889 | 1.000 |
| duplicate posting (n=5) | 100.0% | 80.0% | 0.889 | 1.000 |
| unsupported entry (n=9) | 47.4% | 100.0% | 0.643 | 1.000 |
| unrecorded invoice (n=16) | 64.0% | 100.0% | 0.780 | 1.000 |
| **all exceptions** | **51.5%** | **94.3%** | **0.667** | 1.000 |

Cost: $0.00000 per document · 13 ms per document
