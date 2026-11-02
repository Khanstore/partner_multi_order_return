# User Manual — Multi Order Returns

## Create a return

1. Open **Inventory → Multi Order Returns → Return Orders**.
2. Click **New**.
3. Select the customer first.
4. Add each product and enter the quantity the customer is returning.
5. Click **Process Return** beside the line.

## Select historical documents

In the popup:
1. Select the original completed delivery.
2. Select the posted customer invoice that contains the product.
3. Confirm the quantity.
4. For lot- or serial-tracked products, select the lot/serial delivered to the customer.
5. If the invoice is fully paid, choose:
   - **Leave Credit Note Open (Store Credit)**, or
   - **Register Cash / Bank Refund** and select a cash/bank journal.
6. Click **Confirm Return**.

## After confirmation

The line displays:
- **Return Picking** — open this transfer to inspect and validate the returned stock.
- **Credit Note** — open the posted customer credit note.

The return picking is not automatically validated. Warehouse staff must inspect the goods, verify quantities and lot/serial numbers, then validate the transfer in Inventory. This is important for stock valuation and traceability.

## Important notes

- The original delivery must be completed.
- The invoice must be a posted customer invoice for the same customer and company.
- The quantity cannot exceed the remaining quantity from the selected delivery.
- Tracked goods must use a lot/serial actually delivered on that delivery.
- If the invoice contains multiple separate lines for the same product, this version stops and asks for the invoice data to be simplified or the module extended.
- A posted credit note is not automatically reconciled with the original invoice.
- If a refund payment fails, inspect the posted credit note and complete the payment manually; do not blindly process the return again.

## Troubleshooting

**No historical delivery appears:** verify customer, product, company, outgoing operation type, and that the delivery is Done.

**No invoice appears:** verify the invoice is posted, is a customer invoice, belongs to the customer/company, and includes the product.

**Lot/serial is rejected:** verify it was recorded on the selected delivery move lines.

**Credit note cannot be posted:** ask an accountant to review the journal, income account, taxes, lock date, and permissions.

**Return transfer cannot be validated:** review availability, locations, operation type, lot/serial, and warehouse reservation rules.
