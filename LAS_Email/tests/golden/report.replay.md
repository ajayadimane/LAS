# Golden set report (replay mode)

Cases: 46 · outcome checks passed: 46/46 · tokens: 52500

## Per intent

| intent | precision | recall | TP | FP | FN |
|---|---|---|---|---|---|
| CANCEL_REQUEST | 1.000 | 1.000 | 1 | 0 | 0 |
| CLARIFICATION_REPLY | 1.000 | 1.000 | 2 | 0 | 0 |
| CONFIRM | 1.000 | 1.000 | 2 | 0 | 0 |
| CORRECTION | 1.000 | 1.000 | 2 | 0 | 0 |
| GENERAL_QUESTION | 1.000 | 1.000 | 1 | 0 | 0 |
| MULTI_CUSTOMER | 1.000 | 1.000 | 3 | 0 | 0 |
| NEW_CUSTOMER | 1.000 | 1.000 | 20 | 0 | 0 |
| OUT_OF_SCOPE | 1.000 | 1.000 | 1 | 0 | 0 |
| PAYMENT_PROOF | 0.667 | 1.000 | 2 | 1 | 0 |
| SELECT_SERVICES | 1.000 | 1.000 | 4 | 0 | 0 |
| STATUS_QUERY | 1.000 | 0.875 | 7 | 0 | 1 |
| SUBMIT_DOCUMENT | 1.000 | 1.000 | 2 | 0 | 0 |

## Per field

| field | precision | recall | TP | FP | FN |
|---|---|---|---|---|---|
| amount | 1.000 | 1.000 | 1 | 0 | 0 |
| attachment_filenames | 1.000 | 1.000 | 2 | 0 | 0 |
| confirm_answer | 1.000 | 1.000 | 2 | 0 | 0 |
| correction.new | 1.000 | 1.000 | 2 | 0 | 0 |
| customers.*.email | 1.000 | 1.000 | 1 | 0 | 0 |
| customers.*.mobile | 1.000 | 1.000 | 22 | 0 | 0 |
| customers.*.name | 1.000 | 1.000 | 10 | 0 | 0 |
| customers.*.services | 1.000 | 1.000 | 11 | 0 | 0 |
| menu_choices | 1.000 | 1.000 | 2 | 0 | 0 |
| services | 1.000 | 1.000 | 3 | 0 | 0 |
| txn_ref | 1.000 | 1.000 | 1 | 0 | 0 |

## Outcome failures

none
