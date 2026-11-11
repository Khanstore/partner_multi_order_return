from datetime import date, datetime, time

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare


class ReturnOrderHistoryWizard(models.TransientModel):
    _name = "return.order.history.wizard"
    _description = "Historical Delivery Selection for Multi Order Return"

    return_line_id = fields.Many2one(
        "return.order.line", required=True, readonly=True,
    )
    partner_id = fields.Many2one("res.partner", required=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True, readonly=True)
    company_id = fields.Many2one(
        related="return_line_id.company_id", readonly=True,
    )
    tracking = fields.Selection(related="product_id.tracking", readonly=True)
    qty_returned = fields.Float(
        string="Total Return Quantity", required=True,
        digits="Product Unit of Measure",
    )
    date_filter = fields.Selection(
        [
            ("month", "Month"),
            ("quarter", "Quarter"),
            ("year", "Year"),
            ("all", "All Time"),
        ], string="Delivery Date Range", required=True, default="month",
    )
    filter_month = fields.Selection(
        [(str(month), _(name)) for month, name in enumerate((
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ), start=1)],
        string="Month", default=lambda self: str(fields.Date.context_today(self).month),
    )
    filter_quarter = fields.Selection(
        [("1", "Q1"), ("2", "Q2"), ("3", "Q3"), ("4", "Q4")],
        string="Quarter", default=lambda self: str(
            ((fields.Date.context_today(self).month - 1) // 3) + 1
        ),
    )
    filter_year = fields.Integer(
        string="Year", default=lambda self: fields.Date.context_today(self).year,
    )
    delivery_line_ids = fields.One2many(
        "return.order.history.wizard.line", "wizard_id",
        string="Historical Deliveries",
    )
    selection_note = fields.Char(string="Selection Note", readonly=True)
    allocated_qty = fields.Float(
        string="Allocated Quantity", compute="_compute_allocation_totals",
        digits="Product Unit of Measure",
    )
    selected_delivery_count = fields.Integer(
        compute="_compute_allocation_totals", string="Selected Deliveries",
    )
    allocation_complete = fields.Boolean(
        compute="_compute_allocation_totals", string="Quantity Matched",
    )
    payment_treatment = fields.Selection(
        [
            ("open_credit", "Leave Credit Note Open (Store Credit)"),
            ("refund", "Register Cash / Bank Refund"),
        ], string="Paid Invoice Treatment", default="open_credit", required=True,
    )
    payment_journal_id = fields.Many2one(
        "account.journal", string="Refund Journal",
        domain="[('type', 'in', ['bank', 'cash']), ('company_id', '=', company_id)]",
    )

    @api.model_create_multi
    def create(self, vals_list):
        wizards = super().create(vals_list)
        for wizard in wizards:
            if wizard.return_line_id and not wizard.delivery_line_ids:
                wizard._load_candidates()
        return wizards

    @api.depends("qty_returned", "delivery_line_ids.selected", "delivery_line_ids.allocated_qty")
    def _compute_allocation_totals(self):
        for wizard in self:
            selected = wizard.delivery_line_ids.filtered("selected")
            wizard.allocated_qty = sum(selected.mapped("allocated_qty"))
            wizard.selected_delivery_count = len(
                selected.mapped("picking_id")
            )
            wizard.allocation_complete = (
                wizard.qty_returned > 0
                and float_compare(
                    wizard.allocated_qty,
                    wizard.qty_returned,
                    precision_rounding=wizard.product_id.uom_id.rounding,
                ) == 0
            )

    def _get_date_bounds(self):
        self.ensure_one()
        if self.date_filter == "all":
            return False, False
        if self.date_filter == "month":
            if not self.filter_month or not self.filter_year:
                raise ValidationError(_("Select a month and year."))
            month = int(self.filter_month)
            start = date(self.filter_year, month, 1)
            if month == 12:
                end = date(self.filter_year + 1, 1, 1)
            else:
                end = date(self.filter_year, month + 1, 1)
        elif self.date_filter == "quarter":
            if not self.filter_year or not self.filter_quarter:
                raise ValidationError(_("Select a quarter and year."))
            month = (int(self.filter_quarter) - 1) * 3 + 1
            start = date(self.filter_year, month, 1)
            end_month = month + 3
            end = date(
                self.filter_year + (1 if end_month > 12 else 0),
                end_month - 12 if end_month > 12 else end_month,
                1,
            )
        else:
            if not self.filter_year:
                raise ValidationError(_("Select a year."))
            start = date(self.filter_year, 1, 1)
            end = date(self.filter_year + 1, 1, 1)
        return start, end

    def _get_invoice_for_move(self, move):
        """Return the single posted invoice line tied to the delivered sale line.

        We deliberately do not guess an invoice using partner/product alone: a
        product can appear on several orders at different historical prices.
        """
        sale_line = move.sale_line_id
        if not sale_line:
            return self.env["account.move.line"], self.env["account.move"]
        invoice_lines = self.env["account.move.line"].search([
            ("sale_line_ids", "in", sale_line.ids),
            ("product_id", "=", self.product_id.id),
            ("move_id.move_type", "=", "out_invoice"),
            ("move_id.state", "=", "posted"),
            ("move_id.company_id", "=", self.company_id.id),
            ("move_id.commercial_partner_id", "=", self.partner_id.commercial_partner_id.id),
        ])
        invoices = invoice_lines.mapped("move_id")
        if len(invoices) != 1 or len(invoice_lines) != 1:
            return self.env["account.move.line"], self.env["account.move"]
        return invoice_lines, invoices

    def _get_returned_quantities(self, move):
        """Return completed and pending return quantities in product UoM."""
        product_uom = self.product_id.uom_id
        completed = 0.0
        pending = 0.0
        for returned_move in move.returned_move_ids.filtered(
            lambda item: item.state != "cancel"
        ):
            quantity = (
                returned_move.quantity if returned_move.state == "done"
                else returned_move.product_uom_qty
            )
            converted = returned_move.product_uom._compute_quantity(
                quantity, product_uom, round=False
            )
            if returned_move.state == "done":
                completed += converted
            else:
                pending += converted
        return completed, pending

    def _get_available_for_move(self, move):
        product_uom = self.product_id.uom_id
        delivered = move.product_uom._compute_quantity(
            move.quantity, product_uom, round=False
        )
        completed, pending = self._get_returned_quantities(move)
        return max(delivered - completed - pending, 0.0)

    def _get_lot_rows(self, move):
        """Return lot-specific available quantities for tracked products."""
        if self.product_id.tracking == "none":
            delivered = move.product_uom._compute_quantity(
                move.quantity, self.product_id.uom_id, round=False
            )
            completed, pending = self._get_returned_quantities(move)
            available = max(delivered - completed - pending, 0.0)
            return [(self.env["stock.lot"], delivered, completed, available)]

        rows = []
        for lot in move.move_line_ids.filtered("lot_id").mapped("lot_id"):
            delivered_qty = sum(
                line.product_uom_id._compute_quantity(
                    line.quantity, self.product_id.uom_id, round=False
                )
                for line in move.move_line_ids.filtered(lambda item: item.lot_id == lot)
            )
            completed_qty = 0.0
            pending_qty = 0.0
            for returned_move in move.returned_move_ids.filtered(
                lambda item: item.state != "cancel"
            ):
                lot_lines = returned_move.move_line_ids.filtered(
                    lambda item: item.lot_id == lot
                )
                lot_quantity = sum(
                    line.product_uom_id._compute_quantity(
                        line.quantity, self.product_id.uom_id, round=False
                    )
                    for line in lot_lines
                )
                if returned_move.state == "done":
                    completed_qty += lot_quantity
                else:
                    pending_qty += lot_quantity
            available = max(delivered_qty - completed_qty - pending_qty, 0.0)
            if available:
                rows.append((lot, delivered_qty, completed_qty, available))
        return rows

    def _candidate_values(self):
        self.ensure_one()
        start, end = self._get_date_bounds()
        domain = [
            ("state", "=", "done"),
            ("picking_type_code", "=", "outgoing"),
            ("partner_id", "child_of", self.partner_id.commercial_partner_id.id),
            ("company_id", "=", self.company_id.id),
        ]
        user_tz = pytz.timezone(self.env.user.tz or "UTC")
        if start:
            start_local = user_tz.localize(datetime.combine(start, time.min))
            start_utc = start_local.astimezone(pytz.UTC).replace(tzinfo=None)
            domain.append(("date_done", ">=", start_utc))
        if end:
            end_local = user_tz.localize(datetime.combine(end, time.min))
            end_utc = end_local.astimezone(pytz.UTC).replace(tzinfo=None)
            domain.append(("date_done", "<", end_utc))
        pickings = self.env["stock.picking"].search(domain, order="date_done asc, id asc")
        values = []
        skipped_count = 0
        for picking in pickings:
            moves = picking.move_ids.filtered(
                lambda item: item.state == "done"
                and item.product_id == self.product_id
            )
            # The selection is one row per delivery/lot. Ambiguous multi-move
            # deliveries are rejected rather than silently assigning a price.
            if not moves:
                continue
            if len(moves) != 1:
                skipped_count += 1
                continue
            move = moves[0]
            invoice_lines, invoices = self._get_invoice_for_move(move)
            if not invoice_lines or not invoices:
                skipped_count += 1
                continue
            for lot, delivered, previously_returned, available in self._get_lot_rows(move):
                if available <= 0:
                    continue
                values.append({
                    "picking_id": picking.id,
                    "source_move_id": move.id,
                    "invoice_id": invoices.id,
                    "invoice_line_id": invoice_lines.id,
                    "lot_id": lot.id if lot else False,
                    "delivered_qty": delivered,
                    "previously_returned_qty": previously_returned,
                    "available_qty": available,
                    "price_unit": invoice_lines.price_unit,
                    "discounted_price": invoice_lines.price_unit * (1.0 - (invoice_lines.discount or 0.0) / 100.0),
                })
        return values, skipped_count

    def _load_candidates(self):
        self.ensure_one()
        values, skipped_count = self._candidate_values()
        self.delivery_line_ids = [(5, 0, 0)] + [
            (0, 0, value) for value in values
        ]
        if not values:
            self.selection_note = _(
                "No eligible deliveries were found for this customer, product and date range."
            )
        elif skipped_count:
            self.selection_note = _(
                "%s delivery order(s) were omitted because their product move or "
                "posted invoice relationship was ambiguous."
            ) % skipped_count
        else:
            self.selection_note = False
        self._apply_allocation_method()

    @api.onchange(
        "date_filter", "filter_month", "filter_quarter", "filter_year"
    )
    def _onchange_date_filter(self):
        for wizard in self:
            if wizard.return_line_id:
                wizard._load_candidates()

    @api.onchange("qty_returned")
    def _onchange_total_quantity(self):
        self._apply_allocation_method()

    def _apply_allocation_method(self):
        for wizard in self:
            method = wizard.env["ir.config_parameter"].sudo().get_param(
                "partner_multi_order_return.return_allocation_method", "manual"
            ) or "manual"
            if method == "manual":
                return
            wizard._allocate_automatic(method)

    def _allocate_automatic(self, method):
        for wizard in self:
            remaining = max(wizard.qty_returned or 0.0, 0.0)
            # One2many wizard rows may be unsaved onchange records whose
            # identifiers are NewId objects. Never sort on row.id: compare
            # only stable business values so FIFO allocation also works before
            # the wizard is saved.
            rows = wizard.delivery_line_ids.sorted(
                key=lambda row: (
                    row.delivery_date or row.picking_id.scheduled_date
                    or fields.Datetime.to_datetime("1970-01-01 00:00:00"),
                    row.picking_id.name or "",
                ), reverse=(method == "lifo")
            )
            for row in rows:
                allocation = 0.0
                if row.selected and remaining > 0:
                    allocation = min(row.available_qty, remaining)
                    remaining -= allocation
                row.allocated_qty = allocation

    def _validate_allocation(self):
        self.ensure_one()
        if self.qty_returned <= 0:
            raise ValidationError(_("Total return quantity must be greater than zero."))
        selected = self.delivery_line_ids.filtered("selected")
        if not selected:
            raise ValidationError(_("Select at least one historical delivery."))
        self._apply_allocation_method()
        allocated = sum(selected.mapped("allocated_qty"))
        if float_compare(
            allocated,
            self.qty_returned,
            precision_rounding=self.product_id.uom_id.rounding,
        ) != 0:
            raise ValidationError(_(
                "Selected deliveries do not have enough remaining quantity. "
                "Requested: %(requested)s; allocated: %(allocated)s."
            ) % {"requested": self.qty_returned, "allocated": allocated})
        for row in selected:
            if self.product_id.tracking != "none" and not row.lot_id:
                raise ValidationError(_("A lot/serial number is required for every selected tracked delivery."))
            if row.allocated_qty and row.allocated_qty > row.available_qty:
                raise ValidationError(_("Allocated quantity exceeds available quantity."))
        return selected.filtered(lambda row: row.allocated_qty > 0)

    def _create_return_picking(self, picking, rows):
        """Create one native return picking per original delivery."""
        return_wizard = self.env["stock.return.picking"].with_context(
            active_model="stock.picking",
            active_id=picking.id,
            active_ids=[picking.id],
        ).create({})
        source_move = rows[0].source_move_id
        return_lines = return_wizard.product_return_moves.filtered(
            lambda item: item.move_id == source_move
        )
        if len(return_lines) != 1:
            raise UserError(_(
                "Odoo's return wizard did not provide exactly one return line "
                "for delivery %s.") % picking.name)
        total_qty = sum(rows.mapped("allocated_qty"))
        # Odoo 18's stock.return.picking.line uses the product's default UoM.
        return_lines.quantity = total_qty
        action = return_wizard.action_create_returns()
        return_picking = self.env["stock.picking"].browse(action.get("res_id"))
        if not return_picking.exists():
            raise UserError(_("Odoo did not create a return picking for %s.") % picking.name)

        return_moves = return_picking.move_ids.filtered(
            lambda item: item.product_id == self.product_id
        )
        if len(return_moves) != 1:
            raise UserError(_("Unexpected return move structure for %s.") % picking.name)
        return_move = return_moves[0]
        if return_picking.state == "draft":
            return_picking.action_confirm()
        if self.product_id.tracking != "none":
            return_move.move_line_ids.unlink()
            move_line_commands = []
            for row in rows:
                quantity = self.product_id.uom_id._compute_quantity(
                    row.allocated_qty, return_move.product_uom, round=False
                )
                move_line_commands.append((0, 0, {
                    "product_id": self.product_id.id,
                    "product_uom_id": return_move.product_uom.id,
                    "quantity": quantity,
                    "lot_id": row.lot_id.id,
                    "location_id": return_move.location_id.id,
                    "location_dest_id": return_move.location_dest_id.id,
                }))
            return_move.write({"move_line_ids": move_line_commands})
        else:
            return_picking.action_assign()
        return return_picking

    def _create_credit_note(self, invoice, invoice_line, quantity):
        """Create a partial credit note using the exact historical invoice line."""
        if invoice.state != "posted" or invoice.move_type != "out_invoice":
            raise ValidationError(_("Only posted customer invoices can be credited."))
        if invoice.company_id != self.company_id:
            raise ValidationError(_("The historical invoice belongs to another company."))
        if invoice_line.move_id != invoice or invoice_line.product_id != self.product_id:
            raise ValidationError(_("The selected historical invoice line is inconsistent."))
        if not invoice_line.sale_line_ids:
            raise ValidationError(_(
                "The selected invoice line is not linked to a sale order line. "
                "A credit note cannot be safely connected to the original sale order."
            ))

        vals = {
            "move_type": "out_refund",
            "partner_id": invoice.partner_id.id,
            "company_id": invoice.company_id.id,
            "currency_id": invoice.currency_id.id,
            "journal_id": invoice.journal_id.id,
            "reversed_entry_id": invoice.id,
            "invoice_origin": invoice.name,
            "ref": _("Multi-order return against %s") % invoice.name,
            "invoice_date": fields.Date.context_today(self),
            "invoice_line_ids": [(0, 0, {
                "product_id": invoice_line.product_id.id,
                "name": invoice_line.name,
                "quantity": quantity,
                "product_uom_id": invoice_line.product_uom_id.id,
                "price_unit": invoice_line.price_unit,
                "discount": invoice_line.discount,
                "account_id": invoice_line.account_id.id,
                "tax_ids": [(6, 0, invoice_line.tax_ids.ids)],
                "analytic_distribution": invoice_line.analytic_distribution,
                # Preserve the original sale order line relation. This is what
                # makes the credit note visible in the originating sale order's
                # invoice history and keeps its sale-line traceability intact.
                "sale_line_ids": [(6, 0, invoice_line.sale_line_ids.ids)],
            })],
        }
        credit_note = self.env["account.move"].create(vals)
        credit_note_lines = credit_note.invoice_line_ids.filtered(
            lambda line: line.product_id == invoice_line.product_id
        )
        if len(credit_note_lines) != 1 or set(credit_note_lines.sale_line_ids.ids) != set(invoice_line.sale_line_ids.ids):
            raise ValidationError(_(
                "The credit note line could not retain its original sale order line "
                "relationship. No return documents have been committed."
            ))
        try:
            credit_note.action_post()
        except Exception as error:
            raise UserError(_(
                "Credit note for invoice %s could not be posted. Check accounting "
                "lock dates, journal, accounts, taxes and permissions. Details: %s"
            ) % (invoice.name, str(error))) from error

        if invoice.payment_state == "paid" and self.payment_treatment == "refund":
            if not self.payment_journal_id:
                raise ValidationError(_("Select a cash or bank journal for the refund."))
            if self.payment_journal_id.company_id != self.company_id:
                raise ValidationError(_("The refund journal belongs to another company."))
            try:
                payment_register = self.env["account.payment.register"].with_context(
                    active_model="account.move", active_ids=credit_note.ids,
                ).create({
                    "journal_id": self.payment_journal_id.id,
                    "payment_date": fields.Date.context_today(self),
                })
                payment_register.action_create_payments()
            except Exception as error:
                raise UserError(_(
                    "Refund registration failed for credit note %s. The complete return "
                    "transaction will be rolled back. Check the journal and payment "
                    "configuration. Details: %s"
                ) % (credit_note.name, str(error))) from error
        return credit_note

    def action_confirm_return(self):
        self.ensure_one()
        line = self.return_line_id
        if line.state != "draft":
            raise UserError(_("This return line has already been processed."))
        if line.order_id.partner_id.commercial_partner_id != self.partner_id.commercial_partner_id:
            raise ValidationError(_("The selected customer does not match the return order."))
        selected = self._validate_allocation()

        # Serialize competing returns against the same source moves and invoices.
        # These locks are transaction-scoped and do not bypass ORM business logic.
        source_move_ids = sorted(set(selected.mapped("source_move_id").ids))
        invoice_ids = sorted(set(selected.mapped("invoice_id").ids))
        if source_move_ids:
            self.env.cr.execute(
                "SELECT id FROM stock_move WHERE id = ANY(%s) FOR UPDATE",
                [source_move_ids],
            )
        if invoice_ids:
            self.env.cr.execute(
                "SELECT id FROM account_move WHERE id = ANY(%s) FOR UPDATE",
                [invoice_ids],
            )

        # Re-read quantities after acquiring locks to guard against another
        # operator returning the same delivery concurrently.
        selected.invalidate_recordset(["source_move_id", "picking_id", "invoice_id"])
        selected_moves = selected.mapped("source_move_id")
        selected_moves.invalidate_recordset(["quantity", "returned_move_ids", "move_line_ids"])
        for row in selected:
            current_available = self._get_available_for_move(row.source_move_id)
            if self.product_id.tracking == "none":
                if row.allocated_qty > current_available:
                    raise ValidationError(_("Delivery %s no longer has enough returnable quantity.") % row.picking_id.name)
            else:
                lot_rows = dict(
                    (lot.id, available)
                    for lot, _delivered, _returned, available
                    in self._get_lot_rows(row.source_move_id)
                )
                if row.allocated_qty > lot_rows.get(row.lot_id.id, 0.0):
                    raise ValidationError(_("Lot/serial %s no longer has enough returnable quantity.") % row.lot_id.name)

        picking_rows = {}
        invoice_rows = {}
        for row in selected:
            picking_rows.setdefault(row.picking_id.id, self.env[
                "return.order.history.wizard.line"
            ])
            invoice_rows.setdefault(row.invoice_id.id, self.env[
                "return.order.history.wizard.line"
            ])
            picking_rows[row.picking_id.id] |= row
            invoice_rows[row.invoice_id.id] |= row

        created_pickings = self.env["stock.picking"]
        created_credits = self.env["account.move"]
        created_invoices = self.env["account.move"]
        created_lots = self.env["stock.lot"]
        try:
            with self.env.cr.savepoint():
                for picking_id, rows in picking_rows.items():
                    created_pickings |= self._create_return_picking(
                        self.env["stock.picking"].browse(picking_id), rows
                    )
                for invoice_id, rows in invoice_rows.items():
                    invoice = self.env["account.move"].browse(invoice_id)
                    invoice_lines = rows.mapped("invoice_line_id")
                    if len(invoice_lines) != 1:
                        raise ValidationError(_(
                            "Invoice %s has ambiguous product invoice lines. "
                            "Processing has been stopped safely.") % invoice.name)
                    product_quantity = sum(rows.mapped("allocated_qty"))
                    invoice_line = invoice_lines[0]
                    quantity = self.product_id.uom_id._compute_quantity(
                        product_quantity, invoice_line.product_uom_id, round=False
                    )
                    # Avoid crediting beyond the invoice line's uncredited qty.
                    previous_credits = self.env["account.move"].search([
                        ("move_type", "=", "out_refund"),
                        ("state", "=", "posted"),
                        ("reversed_entry_id", "=", invoice.id),
                    ]).mapped("invoice_line_ids").filtered(
                        lambda item: item.product_id == self.product_id
                    )
                    already_credited = sum(previous_credits.mapped("quantity"))
                    remaining_invoice_qty = max(invoice_line.quantity - already_credited, 0.0)
                    if float_compare(
                        quantity,
                        remaining_invoice_qty,
                        precision_rounding=invoice_line.product_uom_id.rounding,
                    ) > 0:
                        raise ValidationError(_(
                            "Return quantity for invoice %s exceeds its remaining "
                            "uncredited invoice quantity.") % invoice.name)
                    credit_note = self._create_credit_note(
                        invoice, invoice_line, quantity
                    )
                    created_credits |= credit_note
                    created_invoices |= invoice
                created_lots |= selected.mapped("lot_id")
                line.write({
                    "picking_id": created_pickings[:1].id,
                    "picking_ids": [(6, 0, created_pickings.ids)],
                    "invoice_id": created_invoices[:1].id,
                    "invoice_ids": [(6, 0, created_invoices.ids)],
                    "credit_note_id": created_credits[:1].id,
                    "credit_note_ids": [(6, 0, created_credits.ids)],
                    "lot_id": created_lots[:1].id,
                    "lot_ids": [(6, 0, created_lots.ids)],
                    "qty_returned": self.qty_returned,
                    "state": "processed",
                })
                line.order_id._update_state()
        except (UserError, ValidationError):
            raise
        except Exception as error:
            raise UserError(_(
                "Return processing failed. Changes were rolled back. Details: %s"
            ) % str(error)) from error
        return {"type": "ir.actions.act_window_close"}


class ReturnOrderHistoryWizardLine(models.TransientModel):
    _name = "return.order.history.wizard.line"
    _description = "Historical Delivery Candidate"
    _order = "delivery_date, id"

    wizard_id = fields.Many2one(
        "return.order.history.wizard", required=True, ondelete="cascade",
    )
    selected = fields.Boolean(string="Select", default=False)
    picking_id = fields.Many2one("stock.picking", string="Delivery Order", readonly=True)
    delivery_date = fields.Datetime(
        related="picking_id.date_done", string="Delivery Date", readonly=True,
    )
    source_move_id = fields.Many2one("stock.move", required=True, readonly=True)
    invoice_id = fields.Many2one("account.move", string="Historical Invoice", readonly=True)
    invoice_line_id = fields.Many2one("account.move.line", readonly=True)
    lot_id = fields.Many2one("stock.lot", string="Lot / Serial", readonly=True)
    delivered_qty = fields.Float(string="Delivered Qty", digits="Product Unit of Measure", readonly=True)
    previously_returned_qty = fields.Float(string="Previously Returned", digits="Product Unit of Measure", readonly=True)
    available_qty = fields.Float(string="Available Qty", digits="Product Unit of Measure", readonly=True)
    price_unit = fields.Monetary(string="Original Unit Rate", currency_field="currency_id", readonly=True)
    discounted_price = fields.Monetary(string="Rate After Discount", currency_field="currency_id", readonly=True)
    currency_id = fields.Many2one(related="invoice_id.currency_id", readonly=True)
    allocated_qty = fields.Float(string="Allocate Qty", digits="Product Unit of Measure")

    @api.model_create_multi
    def create(self, vals_list):
        """Reject incomplete candidates instead of letting PostgreSQL fail.

        The editable One2many list must preserve the readonly candidate metadata
        (see force_save fields in the wizard view). This guard makes any future
        regression explicit and prevents a row with no source delivery move.
        """
        for vals in vals_list:
            move_id = vals.get("source_move_id")
            picking_id = vals.get("picking_id")
            wizard_id = vals.get("wizard_id")
            invoice_id = vals.get("invoice_id")
            invoice_line_id = vals.get("invoice_line_id")
            if not all((move_id, picking_id, wizard_id, invoice_id, invoice_line_id)):
                raise ValidationError(_(
                    "A historical delivery candidate is incomplete. Reload the "
                    "delivery list; its source stock move and posted invoice "
                    "must be preserved."
                ))

            move = self.env["stock.move"].browse(move_id).exists()
            picking = self.env["stock.picking"].browse(picking_id).exists()
            wizard = self.env["return.order.history.wizard"].browse(wizard_id).exists()
            invoice = self.env["account.move"].browse(invoice_id).exists()
            invoice_line = self.env["account.move.line"].browse(invoice_line_id).exists()
            if not all((move, picking, wizard, invoice, invoice_line)):
                raise ValidationError(_(
                    "The historical delivery, wizard or invoice record no longer exists. "
                    "Reload the candidate list and try again."
                ))
            if (move.state != "done" or move.picking_id != picking
                    or move.product_id != wizard.product_id):
                raise ValidationError(_(
                    "The source stock move must be a completed move for the "
                    "selected product and delivery order."
                ))
            if (invoice.state != "posted" or invoice.move_type != "out_invoice"
                    or invoice_line.move_id != invoice
                    or invoice_line.product_id != wizard.product_id
                    or move.sale_line_id not in invoice_line.sale_line_ids):
                raise ValidationError(_(
                    "The historical invoice line is not valid for this source "
                    "delivery move. Reload the candidate list."
                ))
        return super().create(vals_list)

    @api.onchange("selected")
    def _onchange_selected(self):
        if self.wizard_id:
            self.wizard_id._apply_allocation_method()
