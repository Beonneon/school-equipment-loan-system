# Testing and evaluation

## Automated result

Command: `pytest -q`

Result on 28 August 2026: **12 passed**.

| Test | Expected outcome | Result |
|---|---|---|
| Correct and incorrect login | Valid details open the dashboard; invalid details show a safe error | Pass |
| Unauthenticated route access | Protected pages redirect to sign-in | Pass |
| Missing CSRF token | State-changing request is rejected with HTTP 400 | Pass |
| Borrow, approve and return | Request is created; approval reduces stock; return restores stock | Pass |
| Borrower opens admin route | Request is rejected with HTTP 403 | Pass |
| Due date over 30 days | Request is rejected with a validation message | Pass |
| Reduce stock below checked-out count | Inventory edit is rejected and stock remains consistent | Pass |
| Strong borrower registration | Account is created with borrower role and a password hash | Pass |
| Weak registration password | Account creation is rejected with a clear rule | Pass |
| Add and remove unused category | Category becomes available and can be removed safely | Pass |
| Remove category in use | Removal is blocked until equipment is reassigned | Pass |

## Browser and responsive QA

The running application was inspected in the in-app browser as an administrator.

- Login labels and controls were available through accessible names.
- Dashboard statistics displayed four equipment types and 27 available units.
- Catalogue displayed four seeded categories with stock, location, condition and realistic images.
- Request dialog opened correctly and defaulted to a seven-day due date.
- Request dialog offered hourly pickup choices from 08:00 to 17:00.
- Registration clearly presented password, role and privacy safeguards.
- Return action was changed from a pale badge-like control to a high-contrast navy button.
- Desktop screenshots were checked for clipping, overlap and alignment.
- A 390 x 844 viewport exposed horizontal overflow in the first responsive build.
- The grid was changed to use `minmax(0, 1fr)`, intrinsic control widths were constrained, and mobile card actions were allowed to wrap.
- Recheck result: viewport width 390px; document and body scroll width both 390px (no horizontal overflow).

## Limitations and future tests

- Add end-to-end tests for concurrent approval attempts using multiple database connections.
- Test keyboard focus order and screen-reader output with student users.
- Test a production WSGI server behind HTTPS.
- Add database backup and restore exercises before real school use.
- Conduct usability testing with several students and an equipment coordinator.
