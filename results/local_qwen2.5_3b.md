## local:qwen2.5:3b on 20 invoices / 21 ledger entries

| Field | Accuracy |
|---|---|
| vendor_name | 70.0% |
| invoice_number | 100.0% |
| invoice_date | 80.0% |
| subtotal | 100.0% |
| tax | 100.0% |
| total | 95.0% |
| currency | 100.0% |
| **all fields correct** | **60.0%** |

| Matching | Precision | Recall | F1 | Oracle F1 |
|---|---|---|---|---|
| document ↔ entry links | 100.0% | 95.0% | 0.974 | 1.000 |
| amount mismatch (n=1) | 50.0% | 100.0% | 0.667 | 1.000 |
| period mismatch (n=1) | 100.0% | 100.0% | 1.000 | 1.000 |
| duplicate posting (n=0) | 100.0% | 100.0% | 1.000 | 1.000 |
| unsupported entry (n=1) | 50.0% | 100.0% | 0.667 | 1.000 |
| unrecorded invoice (n=0) | 0.0% | 100.0% | 0.000 | 1.000 |
| **all exceptions** | **50.0%** | **100.0%** | **0.667** | 1.000 |

Cost: $0.00000 per document · 17044 ms per document
