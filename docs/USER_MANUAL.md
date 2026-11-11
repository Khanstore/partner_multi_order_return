# User Manual — Multi Order Returns

## Create a return

1. Open **Inventory → Multi Order Returns → Return Orders**.
2. Click **New**.
3. Select the customer first.
4. Add each product and enter the quantity the customer is returning.
5. Click **Select Deliveries** beside the line.

## Select historical documents

In the popup:
1. Choose **Month**, **Quarter**, **Year** or **All Time**. Month and quarter/year filters use the delivery completion date.
2. Check one or more historical delivery rows. For tracked products, each available lot/serial is shown as a separate row.
3. Enter one total return quantity. The wizard allocates the quantity FIFO by oldest delivery date across selected rows.
4. Review delivered, previously returned, available and allocated quantities. The allocated total must exactly match the requested total.
5. For paid invoices, choose **Leave Credit Note Open (Store Credit)** or **Register Cash / Bank Refund** and select a cash/bank journal.
6. Click **Confirm Return**.

## After confirmation

The line displays links to all generated **Return Pickings** and **Credit Notes**. One return picking is created per selected delivery. Credit notes are grouped by original invoice, so multiple selected deliveries billed on the same invoice produce one credit note for that invoice.

The return picking is not automatically validated. Warehouse staff must inspect the goods, verify quantities and lot/serial numbers, then validate the transfer in Inventory. This is important for stock valuation and traceability.

## Important notes

- The original delivery must be completed.
- The invoice must be a posted customer invoice linked to the delivery's originating sale order line. Ambiguous or unlinked deliveries are not offered for selection.
- The quantity cannot exceed the remaining quantity from the selected delivery.
- Tracked goods must use a lot/serial actually delivered on that delivery.
- If the invoice contains multiple separate lines for the same product, this version stops and asks for the invoice data to be simplified or the module extended.
- A posted credit note is not automatically reconciled with the original invoice.
- If refund registration fails, the complete return transaction is rolled back. Correct the payment journal/configuration before retrying.

## Troubleshooting

**No historical delivery appears:** verify customer, product, company, outgoing operation type, and that the delivery is Done.

**No invoice appears:** verify the invoice is posted, is a customer invoice, belongs to the customer/company, and includes the product.

**Lot/serial is rejected:** verify it was recorded on the selected delivery move lines.

**Credit note cannot be posted:** ask an accountant to review the journal, income account, taxes, lock date, and permissions.

**Return transfer cannot be validated:** review availability, locations, operation type, lot/serial, and warehouse reservation rules.
