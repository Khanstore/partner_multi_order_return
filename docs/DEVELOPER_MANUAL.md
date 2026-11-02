# Developer Manual

## Module architecture

| Component | Model / file | Responsibility |
|---|---|---|
| Return header | `return.order` | Customer, date, company, overall state |
| Return line | `return.order.line` | Product, requested quantity, generated documents |
| Selection wizard | `return.order.history.wizard` | Historical delivery/invoice selection and processing |
| Stock integration | `stock.return.picking` | Native Odoo return move creation |
| Accounting integration | `account.move` | Posted customer credit note |
| Optional payout | `account.payment.register` | Cash/bank refund registration |

## Extension points

- Extend `_compute_eligible_records` to support custom sale/invoice linkage rules or additional picking types.
- Extend `_get_original_move` if the business requires explicit selection among multiple moves for the same product.
- Extend `_get_invoice_line` to expose an invoice-line selector where duplicate product lines are legitimate.
- Extend `_create_credit_note` for localization-specific fiscal position, withholding, analytic, or rounding requirements.
- Extend `_register_refund_payment` for approval workflows or custom payment providers.

## Transaction and error handling

`action_confirm_return` uses a database savepoint. Unhandled errors roll back stock/accounting records created inside that savepoint. Odoo's own constraints, access controls, lock dates, and posting validations are not bypassed.

A payment registration error is raised to the caller. Because the complete operation is enclosed in a database savepoint, the stock and accounting records created in that operation are rolled back when the exception escapes the savepoint. Validate this behavior with the installed Odoo 18 build and payment configuration.

## Stock valuation

The module does not write stock valuation layers or accounting journal items directly. It creates the return through Odoo's stock return wizard and leaves the resulting picking awaiting warehouse validation. On validation, Odoo applies the product category valuation method, stock accounts, valuation layers, and automated valuation entries.

Do not replace this with direct `stock.quant` writes or manually created valuation journal entries.

## Accounting calculation

The credit note line copies the selected invoice line's:
- product and description,
- quantity requested for return,
- unit price,
- discount,
- product UoM,
- income account,
- taxes,
- analytic distribution.

Odoo computes taxes and totals and validates the move on posting. If invoice lines have special price rules or custom fields, extend the values mapping accordingly.

## Required staging tests

1. Untracked storable product; unpaid invoice.
2. Untracked storable product; fully paid invoice; open credit.
3. Fully paid invoice; cash/bank refund.
4. Lot-tracked product with correct and incorrect lot.
5. Serial-tracked product with quantity 1 and quantity greater than 1.
6. Partial return followed by a second partial return.
7. Attempt to exceed the remaining delivered quantity.
8. Invoice in a locked accounting period.
9. Credit note journal/account/tax configuration errors.
10. Multi-company user and company mismatch.
11. Warehouse validation and valuation layer verification.
12. Multiple same-product lines on delivery/invoice (expected safe rejection in this version).

## Odoo 18 compatibility note

Odoo's stock and accounting internals can vary by 18.0 patch level and installed addons. Validate the return wizard field names and payment-register behavior against the deployed server build before production. The addon intentionally uses the ORM and native wizards rather than direct SQL.
