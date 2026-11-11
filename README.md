# Partner Multi Order Return — Odoo 18

Version: `18.0.1.0.1`

## Purpose

Create one customer return document with multiple product lines. Each line is processed against a specific historical outgoing delivery and a posted customer invoice. Odoo's native stock return wizard creates the reverse picking; a customer credit note is generated from the selected invoice line.

## Dependencies

- `sale`
- `stock`
- `account`

## Installation

1. Copy `partner_multi_order_return` into an addons directory included in `addons_path`.
2. Restart the Odoo service.
3. Update the Apps list (developer mode may be required).
4. Install **Partner Multi Order Return**.
5. Open Inventory → Multi Order Returns → Return Orders.

For an Odoo.sh or managed deployment, upload the module to the custom addons repository and update the app list before installing.

## Normal workflow

1. Create a Return Order.
2. Select the customer in the header before adding lines.
3. Add a product and requested quantity.
4. Click **Select Deliveries** on that line.
5. Choose Month, Quarter, Year or All Time to filter by completed delivery date.
6. Check one or more historical delivery rows. For tracked products, select the lot/serial row.
7. Enter one total return quantity. The wizard allocates it FIFO across selected rows.
8. Review delivered, previously returned, available and allocated quantities, then confirm.
9. The system creates one return picking per selected delivery and one credit note per related invoice. Multiple deliveries linked to the same invoice are consolidated into that invoice's credit note.

## Important accounting behavior

- Credit notes are created as `out_refund` moves and posted.
- The generated credit note is not automatically reconciled against the original invoice. This avoids silently changing existing reconciliations.
- The default paid-invoice treatment is an open credit note.
- If cash/bank refund is selected for a paid source invoice, Odoo's `account.payment.register` flow is invoked inside the same transaction. A failure rolls back the complete return operation; correct the journal/payment configuration before retrying.
- Odoo lock dates, journal restrictions, taxes, fiscal positions, and access rules remain effective.

## Operational caveats

This is a production-oriented starting implementation, but it must be validated on a staging database with the exact Odoo 18 build, localization, installed accounting modules, and warehouse routes before production use.

The current wizard intentionally omits ambiguous source data:
- More than one matching product stock move on a delivery.
- More than one matching invoice line for the product on the related invoice.
- Deliveries without a uniquely linked posted invoice line through the originating sale order line.

Return quantities greater than the source move's remaining returnable quantity are blocked. For tracked products, candidate rows are split by lot/serial and quantities are validated per lot. Completed returns and non-cancelled pending return moves are both deducted from available quantities.

The wizard uses Odoo 18's native `stock.return.picking.action_create_returns()` workflow. It creates one return picking per selected original delivery. Untracked returns are assigned where possible; tracked return move lines are populated with the selected lot/serial. Return pickings are not validated/done automatically. Warehouse staff must inspect the return transfer, validate quantities and lot/serial details, and validate the picking through the normal Inventory workflow. The credit note is posted at return confirmation; define an internal control to ensure the physical return is subsequently validated.

## Security

The access CSV grants Inventory Users access to return orders and transient wizard records. Return orders cannot be deleted by ordinary Inventory Users. Company record rules restrict return records and wizard rows to the user's allowed companies. Standard Odoo ACLs and record rules continue to apply to stock and accounting documents. Consider restricting menu and model access to a dedicated return-processing group in deployments with stricter segregation of duties.

## Upgrade

Increment the `version` in `__manifest__.py`, replace the addon directory, restart Odoo, then upgrade the module from Apps or with `-u partner_multi_order_return`.

## Support checklist

- Confirm the source delivery is done and belongs to the customer.
- Confirm the source invoice is posted and belongs to the same company/customer.
- Confirm invoice line unit price and taxes are correct for the credit note.
- Confirm return picking source/destination locations and lot/serial.
- Confirm credit note posting date is permitted by accounting lock dates.
- Confirm refund journal and payment method configuration before selecting cash/bank refund.


## Purchase returns (18.0.1.0.9)

The module now includes a separate **Purchase Returns** menu under **Multi Order Returns**. A purchase return line loads completed incoming receipts for the selected vendor and product, then allocates only quantities connected to posted vendor bills and original purchase order lines. FIFO, LIFO and Manual use the same setting as customer returns.

The confirmation creates native Odoo supplier-return pickings and posted vendor credit notes. The credit notes reverse the selected vendor bills and preserve the original `purchase_line_id` relationship. Tracked products use lot/serial candidate rows. For paid vendor bills, the wizard can leave the vendor credit note open or register a vendor refund using a cash/bank journal.

The workflow intentionally skips receipts without an unambiguous source purchase line and posted vendor bill. It does not guess a bill based only on vendor and product.
