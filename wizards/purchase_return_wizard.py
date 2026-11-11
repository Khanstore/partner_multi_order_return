from datetime import date, datetime, time

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare


class PurchaseReturnHistoryWizard(models.TransientModel):
    _name = "purchase.return.history.wizard"
    _description = "Historical Receipt and Vendor Bill Selection"

    return_line_id = fields.Many2one("purchase.return.order.line", required=True, readonly=True)
    partner_id = fields.Many2one("res.partner", required=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True, readonly=True)
    company_id = fields.Many2one(related="return_line_id.company_id", readonly=True)
    tracking = fields.Selection(related="product_id.tracking", readonly=True)
    qty_returned = fields.Float(required=True, digits="Product Unit of Measure")
    date_filter = fields.Selection([("month", "Month"), ("quarter", "Quarter"), ("year", "Year"), ("all", "All Time")], default="month", required=True)
    filter_month = fields.Selection([(str(m), _(n)) for m, n in enumerate(("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)], default=lambda self: str(fields.Date.context_today(self).month))
    filter_quarter = fields.Selection([("1", "Q1"), ("2", "Q2"), ("3", "Q3"), ("4", "Q4")], default=lambda self: str((fields.Date.context_today(self).month - 1) // 3 + 1))
    filter_year = fields.Integer(default=lambda self: fields.Date.context_today(self).year)
    receipt_line_ids = fields.One2many("purchase.return.history.wizard.line", "wizard_id", string="Historical Receipts")
    selection_note = fields.Char(readonly=True)
    allocated_qty = fields.Float(compute="_compute_totals", digits="Product Unit of Measure")
    selected_receipt_count = fields.Integer(compute="_compute_totals")
    allocation_complete = fields.Boolean(compute="_compute_totals")
    payment_treatment = fields.Selection(
        [("open_credit", "Leave Vendor Credit Note Open"), ("refund", "Register Vendor Refund")],
        string="Paid Bill Treatment", default="open_credit", required=True,
    )
    payment_journal_id = fields.Many2one(
        "account.journal", string="Refund Journal",
        domain="[('type', 'in', ['bank', 'cash']), ('company_id', '=', company_id)]",
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for wizard in records:
            if wizard.return_line_id and not wizard.receipt_line_ids:
                wizard._load_candidates()
        return records

    @api.depends("qty_returned", "receipt_line_ids.selected", "receipt_line_ids.allocated_qty")
    def _compute_totals(self):
        for wizard in self:
            selected = wizard.receipt_line_ids.filtered("selected")
            wizard.allocated_qty = sum(selected.mapped("allocated_qty"))
            wizard.selected_receipt_count = len(selected.mapped("picking_id"))
            wizard.allocation_complete = bool(wizard.qty_returned > 0 and float_compare(
                wizard.allocated_qty, wizard.qty_returned,
                precision_rounding=wizard.product_id.uom_id.rounding) == 0)

    def _date_bounds(self):
        self.ensure_one()
        if self.date_filter == "all":
            return False, False
        if self.date_filter == "month":
            month = int(self.filter_month)
            start = date(self.filter_year, month, 1)
            end = date(self.filter_year + (month == 12), 1 if month == 12 else month + 1, 1)
        elif self.date_filter == "quarter":
            month = (int(self.filter_quarter) - 1) * 3 + 1
            end_month = month + 3
            start = date(self.filter_year, month, 1)
            end = date(self.filter_year + (end_month > 12), end_month - 12 if end_month > 12 else end_month, 1)
        else:
            start, end = date(self.filter_year, 1, 1), date(self.filter_year + 1, 1, 1)
        return start, end

    def _lot_availability(self, move):
        product_uom = self.product_id.uom_id
        if self.product_id.tracking == "none":
            delivered = move.product_uom._compute_quantity(move.quantity, product_uom, round=False)
            done = pending = 0.0
            for returned in move.returned_move_ids.filtered(lambda m: m.state != "cancel"):
                qty = returned.quantity if returned.state == "done" else returned.product_uom_qty
                qty = returned.product_uom._compute_quantity(qty, product_uom, round=False)
                if returned.state == "done": done += qty
                else: pending += qty
            return [(self.env["stock.lot"], delivered, done + pending, max(delivered - done - pending, 0.0))]
        result = []
        for lot in move.move_line_ids.filtered("lot_id").mapped("lot_id"):
            delivered = sum(x.product_uom_id._compute_quantity(x.quantity, product_uom, round=False)
                            for x in move.move_line_ids.filtered(lambda ml: ml.lot_id == lot))
            returned_qty = 0.0
            for returned in move.returned_move_ids.filtered(lambda m: m.state != "cancel"):
                returned_qty += sum(x.product_uom_id._compute_quantity(x.quantity, product_uom, round=False)
                                    for x in returned.move_line_ids.filtered(lambda ml: ml.lot_id == lot))
            available = max(delivered - returned_qty, 0.0)
            if available:
                result.append((lot, delivered, returned_qty, available))
        return result

    def _candidate_values(self):
        self.ensure_one()
        start, end = self._date_bounds()
        domain = [("state", "=", "done"), ("picking_type_code", "=", "incoming"),
                  ("partner_id", "child_of", self.partner_id.commercial_partner_id.id),
                  ("company_id", "=", self.company_id.id)]
        tz = pytz.timezone(self.env.user.tz or "UTC")
        if start:
            domain.append(("date_done", ">=", tz.localize(datetime.combine(start, time.min)).astimezone(pytz.UTC).replace(tzinfo=None)))
        if end:
            domain.append(("date_done", "<", tz.localize(datetime.combine(end, time.min)).astimezone(pytz.UTC).replace(tzinfo=None)))
        pickings = self.env["stock.picking"].search(domain, order="date_done asc, id asc")
        values, skipped = [], 0
        # The same product may appear on several purchase lines/moves in one
        # receipt. Keep each source move distinct; never discard the receipt.
        bill_line_remaining = {}
        for picking in pickings:
            moves = picking.move_ids.filtered(
                lambda m: m.state == "done" and m.product_id == self.product_id and m.purchase_line_id
            )
            for move in moves:
                po_line = move.purchase_line_id
                bill_lines = self.env["account.move.line"].search([
                    ("purchase_line_id", "=", po_line.id), ("product_id", "=", self.product_id.id),
                    ("move_id.move_type", "=", "in_invoice"), ("move_id.state", "=", "posted"),
                    ("move_id.company_id", "=", self.company_id.id),
                    ("move_id.commercial_partner_id", "=", self.partner_id.commercial_partner_id.id),
                ], order="id")
                # Odoo 18 cannot translate dotted relational fields in a
                # model search order (e.g. move_id.invoice_date) to SQL.
                # Sort the retrieved lines in Python using their parent bill.
                bill_lines = bill_lines.sorted(
                    key=lambda line: (
                        line.move_id.invoice_date or line.move_id.date or fields.Date.today(),
                        line.move_id.id,
                        line.id,
                    )
                )
                if not bill_lines:
                    skipped += 1
                    continue
                lot_rows = self._lot_availability(move)
                stock_remaining = sum(x[3] for x in lot_rows)
                for bill_line in bill_lines:
                    if bill_line.id not in bill_line_remaining:
                        refunds = self.env["account.move"].search([
                            ("move_type", "=", "in_refund"), ("state", "=", "posted"),
                            ("reversed_entry_id", "=", bill_line.move_id.id)])
                        already = sum(refunds.mapped("invoice_line_ids").filtered(
                            lambda l: l.purchase_line_id == po_line and l.product_id == self.product_id).mapped("quantity"))
                        bill_line_remaining[bill_line.id] = max(bill_line.quantity - already, 0.0)
                    cap = min(stock_remaining, bill_line_remaining[bill_line.id])
                    if cap <= 0:
                        continue
                    for lot, delivered, returned, available in lot_rows:
                        take = min(available, cap)
                        if take <= 0:
                            continue
                        values.append({
                            "picking_id": picking.id, "source_move_id": move.id,
                            "purchase_line_id": po_line.id, "bill_id": bill_line.move_id.id,
                            "bill_line_id": bill_line.id, "lot_id": lot.id if lot else False,
                            "delivered_qty": delivered, "previously_returned_qty": returned,
                            "available_qty": take, "price_unit": bill_line.price_unit,
                        })
                        cap -= take
                        stock_remaining -= take
                        bill_line_remaining[bill_line.id] -= take
                        if cap <= 0:
                            break
                    if stock_remaining <= 0:
                        break
        return values, skipped

    def _load_candidates(self):
        self.ensure_one()
        values, skipped = self._candidate_values()
        self.receipt_line_ids = [(5, 0, 0)] + [(0, 0, v) for v in values]
        if not values:
            self.selection_note = _("No eligible incoming receipts with remaining quantity and a linked posted vendor bill were found.")
        elif skipped:
            self.selection_note = _("%s source move(s) were omitted because they were not linked to a posted vendor bill.") % skipped
        else:
            self.selection_note = False
        self._apply_allocation()

    @api.onchange("date_filter", "filter_month", "filter_quarter", "filter_year")
    def _onchange_date_filter(self):
        for wizard in self:
            if wizard.return_line_id:
                wizard._load_candidates()

    @api.onchange("qty_returned")
    def _onchange_qty(self):
        self._apply_allocation()

    def _apply_allocation(self):
        method = self.env["ir.config_parameter"].sudo().get_param("partner_multi_order_return.return_allocation_method", "manual") or "manual"
        if method == "manual":
            return
        for wizard in self:
            remaining = max(wizard.qty_returned or 0.0, 0.0)
            rows = wizard.receipt_line_ids.sorted(key=lambda r: (r.receipt_date or fields.Datetime.now(), r.picking_id.name or ""), reverse=method == "lifo")
            for row in rows:
                row.allocated_qty = min(row.available_qty, remaining) if row.selected and remaining > 0 else 0.0
                remaining -= row.allocated_qty

    def _validate_allocation(self):
        self.ensure_one()
        if self.qty_returned <= 0:
            raise ValidationError(_("Return quantity must be greater than zero."))
        selected = self.receipt_line_ids.filtered("selected")
        self._apply_allocation()
        if not selected or float_compare(sum(selected.mapped("allocated_qty")), self.qty_returned, precision_rounding=self.product_id.uom_id.rounding):
            raise ValidationError(_("Selected receipts do not allocate the requested quantity. Check the available and allocated quantities."))
        for row in selected:
            if row.allocated_qty < 0 or row.allocated_qty > row.available_qty:
                raise ValidationError(_("Allocated quantity exceeds the available quantity."))
            if self.product_id.tracking != "none" and not row.lot_id:
                raise ValidationError(_("A lot or serial number is required for every tracked return quantity."))
            if self.product_id.tracking == "serial" and float_compare(row.allocated_qty, 1.0, precision_rounding=self.product_id.uom_id.rounding) > 0:
                raise ValidationError(_("A serial number can only be returned once per allocation row."))
        return selected.filtered(lambda r: r.allocated_qty > 0)

    def _create_return_picking(self, picking, rows):
        wizard = self.env["stock.return.picking"].with_context(
            active_model="stock.picking", active_id=picking.id, active_ids=[picking.id]
        ).create({})
        source_moves = rows.mapped("source_move_id")
        return_lines = wizard.product_return_moves.filtered(lambda l: l.move_id in source_moves)
        if set(return_lines.mapped("move_id").ids) != set(source_moves.ids):
            raise UserError(_("Odoo did not provide a return line for every selected source move in receipt %s.") % picking.name)
        for return_line in return_lines:
            source_rows = rows.filtered(lambda r: r.source_move_id == return_line.move_id)
            return_line.quantity = sum(source_rows.mapped("allocated_qty"))
        action = wizard.action_create_returns()
        returned = self.env["stock.picking"].browse(action.get("res_id")).exists()
        if not returned:
            raise UserError(_("Odoo did not create a supplier return picking for %s.") % picking.name)
        if returned.state == "draft":
            returned.action_confirm()
        for source_move in source_moves:
            source_rows = rows.filtered(lambda r: r.source_move_id == source_move)
            return_moves = returned.move_ids.filtered(lambda m: m.origin_returned_move_id == source_move)
            if len(return_moves) != 1:
                raise UserError(_("Unexpected supplier return move structure for %s.") % picking.name)
            move = return_moves[0]
            if self.product_id.tracking != "none":
                move.move_line_ids.unlink()
                commands = []
                for row in source_rows:
                    qty = self.product_id.uom_id._compute_quantity(row.allocated_qty, move.product_uom, round=False)
                    commands.append((0, 0, {
                        "product_id": self.product_id.id, "product_uom_id": move.product_uom.id,
                        "quantity": qty, "lot_id": row.lot_id.id,
                        "location_id": move.location_id.id, "location_dest_id": move.location_dest_id.id,
                    }))
                move.write({"move_line_ids": commands})
        if self.product_id.tracking == "none":
            returned.action_assign()
        return returned

    def _create_vendor_refund(self, bill, rows):
        if bill.state != "posted" or bill.move_type != "in_invoice" or bill.company_id != self.company_id:
            raise ValidationError(_("Only posted vendor bills in the current company can be refunded."))
        grouped = {}
        for row in rows:
            grouped[row.bill_line_id.id] = grouped.get(row.bill_line_id.id, 0.0) + row.allocated_qty
        commands = []
        for line_id, product_qty in grouped.items():
            original = self.env["account.move.line"].browse(line_id)
            if original.move_id != bill or original.purchase_line_id != rows.filtered(lambda r: r.bill_line_id.id == line_id)[0].purchase_line_id:
                raise ValidationError(_("The selected vendor bill line is not linked to its source purchase order line."))
            qty = self.product_id.uom_id._compute_quantity(product_qty, original.product_uom_id, round=False)
            commands.append((0, 0, {
                "product_id": original.product_id.id, "name": original.name,
                "quantity": qty, "product_uom_id": original.product_uom_id.id,
                "price_unit": original.price_unit, "discount": original.discount,
                "account_id": original.account_id.id, "tax_ids": [(6, 0, original.tax_ids.ids)],
                "analytic_distribution": original.analytic_distribution,
                "purchase_line_id": original.purchase_line_id.id,
            }))
        refund = self.env["account.move"].create({
            "move_type": "in_refund", "partner_id": bill.partner_id.id,
            "company_id": bill.company_id.id, "currency_id": bill.currency_id.id,
            "journal_id": bill.journal_id.id, "reversed_entry_id": bill.id,
            "invoice_origin": bill.name, "ref": _("Purchase return against %s") % bill.name,
            "invoice_date": fields.Date.context_today(self), "invoice_line_ids": commands,
        })
        for line_id in grouped:
            original = self.env["account.move.line"].browse(line_id)
            refund_lines = refund.invoice_line_ids.filtered(lambda l: l.product_id == original.product_id and l.purchase_line_id == original.purchase_line_id)
            if not refund_lines or any(l.purchase_line_id != original.purchase_line_id for l in refund_lines):
                raise ValidationError(_("Vendor credit note could not retain the original purchase order line link."))
        refund.action_post()
        if bill.payment_state == "paid" and self.payment_treatment == "refund":
            if not self.payment_journal_id or self.payment_journal_id.company_id != self.company_id:
                raise ValidationError(_("Select a cash or bank journal belonging to this company to register the vendor refund."))
            try:
                payment_register = self.env["account.payment.register"].with_context(
                    active_model="account.move", active_ids=refund.ids,
                ).create({
                    "journal_id": self.payment_journal_id.id,
                    "payment_date": fields.Date.context_today(self),
                })
                payment_register.action_create_payments()
            except Exception as error:
                raise UserError(_("Vendor refund payment could not be registered. The complete return transaction will be rolled back. Details: %s") % str(error)) from error
        return refund

    def action_confirm_return(self):
        self.ensure_one()
        line = self.return_line_id
        if line.state != "draft":
            raise UserError(_("This purchase return line has already been processed."))
        if line.order_id.partner_id.commercial_partner_id != self.partner_id.commercial_partner_id:
            raise ValidationError(_("The selected vendor does not match the purchase return."))
        selected = self._validate_allocation()
        move_ids = sorted(set(selected.mapped("source_move_id").ids))
        bill_ids = sorted(set(selected.mapped("bill_id").ids))
        self.env.cr.execute("SELECT id FROM stock_move WHERE id = ANY(%s) FOR UPDATE", [move_ids])
        self.env.cr.execute("SELECT id FROM account_move WHERE id = ANY(%s) FOR UPDATE", [bill_ids])
        selected.mapped("source_move_id").invalidate_recordset(["quantity", "returned_move_ids", "move_line_ids"])
        # Revalidate the aggregate per source move/lot, not row-by-row: one
        # receipt lot can appear in more than one vendor-bill allocation row.
        grouped_move_lot = {}
        for row in selected:
            key = (row.source_move_id.id, row.lot_id.id if row.lot_id else False)
            grouped_move_lot[key] = grouped_move_lot.get(key, 0.0) + row.allocated_qty
        for (move_id, lot_id), allocated in grouped_move_lot.items():
            move = self.env["stock.move"].browse(move_id)
            available = dict((lot.id if lot else False, qty) for lot, _d, _r, qty in self._lot_availability(move))
            if allocated > available.get(lot_id, 0.0):
                raise ValidationError(_("Receipt %s no longer has the selected returnable quantity. Reload the receipt list.") % move.picking_id.name)
        # A vendor bill line may be refunded by another return transaction.
        # Check its remaining refundable quantity again after locking the bill.
        grouped_bill_line = {}
        for row in selected:
            grouped_bill_line[row.bill_line_id.id] = grouped_bill_line.get(row.bill_line_id.id, 0.0) + row.allocated_qty
        for bill_line_id, product_qty in grouped_bill_line.items():
            bill_line = self.env["account.move.line"].browse(bill_line_id)
            posted_refunds = self.env["account.move"].search([
                ("move_type", "=", "in_refund"), ("state", "=", "posted"),
                ("reversed_entry_id", "=", bill_line.move_id.id),
            ])
            refunded_qty = sum(posted_refunds.mapped("invoice_line_ids").filtered(
                lambda l: l.purchase_line_id == bill_line.purchase_line_id and l.product_id == bill_line.product_id
            ).mapped("quantity"))
            requested_bill_qty = self.product_id.uom_id._compute_quantity(product_qty, bill_line.product_uom_id, round=False)
            if float_compare(requested_bill_qty, max(bill_line.quantity - refunded_qty, 0.0), precision_rounding=bill_line.product_uom_id.rounding) > 0:
                raise ValidationError(_("Vendor bill %s no longer has enough uncredited quantity on the selected purchase line.") % bill_line.move_id.name)
        by_picking, by_bill = {}, {}
        for row in selected:
            by_picking.setdefault(row.picking_id.id, self.env["purchase.return.history.wizard.line"])
            by_picking[row.picking_id.id] |= row
            by_bill.setdefault(row.bill_id.id, self.env["purchase.return.history.wizard.line"])
            by_bill[row.bill_id.id] |= row
        created_pickings = self.env["stock.picking"]
        created_refunds = self.env["account.move"]
        try:
            with self.env.cr.savepoint():
                for picking_id, rows in by_picking.items():
                    created_pickings |= self._create_return_picking(self.env["stock.picking"].browse(picking_id), rows)
                for bill_id, rows in by_bill.items():
                    created_refunds |= self._create_vendor_refund(self.env["account.move"].browse(bill_id), rows)
                line.write({"picking_ids": [(6, 0, created_pickings.ids)],
                            "bill_ids": [(6, 0, sorted(set(selected.mapped("bill_id").ids)))],
                            "refund_ids": [(6, 0, created_refunds.ids)],
                            "lot_ids": [(6, 0, sorted(set(selected.mapped("lot_id").ids)))],
                            "qty_returned": self.qty_returned, "state": "processed"})
                line.order_id._update_state()
        except (UserError, ValidationError):
            raise
        except Exception as error:
            raise UserError(_("Purchase return failed and all changes were rolled back. Details: %s") % str(error)) from error
        return {"type": "ir.actions.act_window_close"}


class PurchaseReturnHistoryWizardLine(models.TransientModel):
    _name = "purchase.return.history.wizard.line"
    _description = "Historical Receipt Candidate"
    _order = "receipt_date, id"

    wizard_id = fields.Many2one("purchase.return.history.wizard", required=True, ondelete="cascade")
    selected = fields.Boolean(default=False)
    picking_id = fields.Many2one("stock.picking", string="Receipt", readonly=True)
    receipt_date = fields.Datetime(related="picking_id.date_done", readonly=True)
    source_move_id = fields.Many2one("stock.move", required=True, readonly=True)
    purchase_line_id = fields.Many2one("purchase.order.line", required=True, readonly=True)
    bill_id = fields.Many2one("account.move", string="Vendor Bill", readonly=True)
    bill_line_id = fields.Many2one("account.move.line", required=True, readonly=True)
    lot_id = fields.Many2one("stock.lot", string="Lot / Serial", readonly=True)
    delivered_qty = fields.Float(digits="Product Unit of Measure", readonly=True)
    previously_returned_qty = fields.Float(string="Previously Returned", digits="Product Unit of Measure", readonly=True)
    available_qty = fields.Float(digits="Product Unit of Measure", readonly=True)
    price_unit = fields.Monetary(currency_field="currency_id", readonly=True)
    currency_id = fields.Many2one(related="bill_id.currency_id", readonly=True)
    allocated_qty = fields.Float(string="Allocate Qty", digits="Product Unit of Measure")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            required = ("wizard_id", "picking_id", "source_move_id", "purchase_line_id", "bill_id", "bill_line_id")
            if not all(vals.get(k) for k in required):
                raise ValidationError(_("A historical receipt candidate is incomplete. Reload the receipt list."))
            move = self.env["stock.move"].browse(vals["source_move_id"]).exists()
            picking = self.env["stock.picking"].browse(vals["picking_id"]).exists()
            wizard = self.env["purchase.return.history.wizard"].browse(vals["wizard_id"]).exists()
            bill = self.env["account.move"].browse(vals["bill_id"]).exists()
            bill_line = self.env["account.move.line"].browse(vals["bill_line_id"]).exists()
            po_line = self.env["purchase.order.line"].browse(vals["purchase_line_id"]).exists()
            if not all((move, picking, wizard, bill, bill_line, po_line)) or move.state != "done" or move.picking_id != picking or move.purchase_line_id != po_line or bill.state != "posted" or bill.move_type != "in_invoice" or bill_line.move_id != bill or bill_line.purchase_line_id != po_line or bill_line.product_id != wizard.product_id:
                raise ValidationError(_("The selected receipt, purchase order line or vendor bill relationship is invalid. Reload the candidates."))
        return super().create(vals_list)

    @api.onchange("selected")
    def _onchange_selected(self):
        if self.wizard_id:
            self.wizard_id._apply_allocation()
