from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ReturnOrderHistoryWizard(models.TransientModel):
    _name = "return.order.history.wizard"
    _description = "Historical Return Selection"

    return_line_id = fields.Many2one(
        "return.order.line", required=True, readonly=True,
    )
    partner_id = fields.Many2one("res.partner", required=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True, readonly=True)
    picking_id = fields.Many2one(
        "stock.picking", string="Original Delivery", required=True,
        domain="[('id', 'in', eligible_picking_ids)]",
    )
    eligible_picking_ids = fields.Many2many(
        "stock.picking", compute="_compute_eligible_records",
    )
    invoice_id = fields.Many2one(
        "account.move", string="Historical Customer Invoice", required=True,
        domain="[('id', 'in', eligible_invoice_ids)]",
    )
    eligible_invoice_ids = fields.Many2many(
        "account.move", compute="_compute_eligible_records",
    )
    qty_returned = fields.Float(
        string="Quantity", required=True, digits="Product Unit of Measure",
    )
    lot_id = fields.Many2one(
        "stock.lot", string="Lot / Serial Number",
        domain="[('product_id', '=', product_id), ('company_id', 'in', [False, company_id])]",
    )
    tracking = fields.Selection(related="product_id.tracking", readonly=True)
    company_id = fields.Many2one(
        related="return_line_id.company_id", readonly=True,
    )
    payment_treatment = fields.Selection(
        [
            ("open_credit", "Leave Credit Note Open (Store Credit)"),
            ("refund", "Register Cash / Bank Refund"),
        ],
        string="Paid Invoice Treatment", default="open_credit", required=True,
    )
    payment_journal_id = fields.Many2one(
        "account.journal", string="Refund Journal",
        domain="[('type', 'in', ['bank', 'cash']), ('company_id', '=', company_id)]",
    )

    @api.depends("partner_id", "product_id", "company_id")
    def _compute_eligible_records(self):
        Picking = self.env["stock.picking"]
        Move = self.env["stock.move"]
        Invoice = self.env["account.move"]
        for wizard in self:
            picking_ids = []
            invoice_ids = []
            if wizard.partner_id and wizard.product_id:
                moves = Move.search([
                    ("product_id", "=", wizard.product_id.id),
                    ("state", "=", "done"),
                    ("picking_id.partner_id", "child_of", wizard.partner_id.id),
                    ("picking_id.picking_type_code", "=", "outgoing"),
                    ("picking_id.company_id", "=", wizard.company_id.id),
                ])
                picking_ids = moves.mapped("picking_id").ids
                invoice_ids = Invoice.search([
                    ("move_type", "=", "out_invoice"),
                    ("state", "=", "posted"),
                    ("commercial_partner_id", "=", wizard.partner_id.commercial_partner_id.id),
                    ("company_id", "=", wizard.company_id.id),
                    ("invoice_line_ids.product_id", "=", wizard.product_id.id),
                ]).ids
            wizard.eligible_picking_ids = Picking.browse(picking_ids)
            wizard.eligible_invoice_ids = Invoice.browse(invoice_ids)

    @api.onchange("picking_id")
    def _onchange_picking_id(self):
        self.lot_id = False
        if self.picking_id and self.product_id:
            matching_moves = self.picking_id.move_ids.filtered(
                lambda move: move.product_id == self.product_id and move.state == "done"
            )
            if len(matching_moves) == 1:
                move = matching_moves[0]
                self.qty_returned = min(
                    self.qty_returned or move.quantity,
                    move.quantity,
                )

    @api.onchange("invoice_id")
    def _onchange_invoice_id(self):
        if self.invoice_id and self.invoice_id.payment_state == "paid":
            self.payment_treatment = "open_credit"

    def _get_original_move(self):
        self.ensure_one()
        moves = self.picking_id.move_ids.filtered(
            lambda move: move.product_id == self.product_id and move.state == "done"
        )
        if not moves:
            raise UserError(
                _("The selected delivery does not contain the selected product.")
            )
        if len(moves) > 1:
            raise UserError(
                _("The delivery contains multiple moves for this product. "
                  "This version requires a delivery with one matching product move.")
            )
        return moves

    def _validate_selection(self):
        self.ensure_one()
        line = self.return_line_id
        if line.state != "draft":
            raise UserError(_("This return line is already processed."))
        if self.picking_id.partner_id.commercial_partner_id != self.partner_id.commercial_partner_id:
            raise ValidationError(_("The delivery does not belong to the selected customer."))
        if self.invoice_id.commercial_partner_id != self.partner_id.commercial_partner_id:
            raise ValidationError(_("The invoice does not belong to the selected customer."))
        if self.invoice_id.state != "posted" or self.invoice_id.move_type != "out_invoice":
            raise ValidationError(_("Select a posted customer invoice."))
        if self.invoice_id.company_id != line.company_id:
            raise ValidationError(_("The invoice belongs to a different company."))
        if self.qty_returned <= 0:
            raise ValidationError(_("Return quantity must be greater than zero."))
        original_move = self._get_original_move()
        already_returned = sum(
            original_move.returned_move_ids.filtered(
                lambda move: move.state == "done"
            ).mapped("quantity")
        )
        requested_in_move_uom = self.product_id.uom_id._compute_quantity(
            self.qty_returned, original_move.product_uom
        )
        remaining = max(original_move.quantity - already_returned, 0.0)
        if requested_in_move_uom > remaining:
            raise ValidationError(
                _("Requested quantity %(requested)s exceeds the remaining "
                  "returnable quantity %(remaining)s.")
                % {"requested": requested_in_move_uom, "remaining": remaining}
            )
        invoice_lines = self.invoice_id.invoice_line_ids.filtered(
            lambda inv_line: inv_line.product_id == self.product_id
        )
        if not invoice_lines:
            raise ValidationError(
                _("The selected invoice does not contain the selected product.")
            )
        if self.product_id.tracking != "none":
            if not self.lot_id:
                raise ValidationError(_("Select a lot or serial number for this tracked product."))
            delivered_lots = original_move.move_line_ids.mapped("lot_id")
            if self.lot_id not in delivered_lots:
                raise ValidationError(
                    _("The selected lot/serial was not delivered on this picking.")
                )
            if self.product_id.tracking == "serial" and self.qty_returned != 1:
                raise ValidationError(_("Serial-tracked products must be returned one unit at a time."))
        if self.invoice_id.payment_state == "paid" and self.payment_treatment == "refund":
            if not self.payment_journal_id:
                raise ValidationError(_("Select a cash or bank journal for the refund."))
        return original_move

    def _create_stock_return(self, original_move):
        self.ensure_one()
        return_wizard = self.env["stock.return.picking"].with_context(
            active_model="stock.picking",
            active_id=self.picking_id.id,
            active_ids=[self.picking_id.id],
        ).create({})
        return_line = return_wizard.product_return_moves.filtered(
            lambda item: item.move_id == original_move
        )
        if len(return_line) != 1:
            raise UserError(_("Could not identify the stock return wizard line."))
        return_line.quantity = self.product_id.uom_id._compute_quantity(
            self.qty_returned, original_move.product_uom
        )
        result = return_wizard._create_return()
        new_picking_id = result.id
        if not new_picking_id:
            raise UserError(_("Odoo did not create a return picking."))
        return_picking = self.env["stock.picking"].browse(new_picking_id)
        return_picking.action_confirm()
        return_picking.action_assign()

        return_moves = return_picking.move_ids.filtered(
            lambda move: move.product_id == self.product_id
        )
        if len(return_moves) != 1:
            raise UserError(_("The generated return picking has an unexpected move structure."))
        move = return_moves[0]
        if self.product_id.tracking != "none":
            move_lines = move.move_line_ids
            if move_lines:
                move_lines.unlink()
            move.write({
                "move_line_ids": [(0, 0, {
                    "product_id": self.product_id.id,
                    "product_uom_id": move.product_uom.id,
                    "quantity": self.qty_returned,
                    "lot_id": self.lot_id.id,
                    "location_id": move.location_id.id,
                    "location_dest_id": move.location_dest_id.id,
                })],
            })
        return return_picking

    def _get_invoice_line(self):
        self.ensure_one()
        lines = self.invoice_id.invoice_line_ids.filtered(
            lambda line: line.product_id == self.product_id
        )
        if not lines:
            raise UserError(_("No invoice line was found for this product."))
        if len(lines) > 1:
            raise UserError(
                _("The invoice has multiple lines for this product. "
                  "Please consolidate them or extend the module to select an invoice line.")
            )
        return lines[0]

    def _create_credit_note(self):
        self.ensure_one()
        invoice_line = self._get_invoice_line()
        if self.invoice_id.state != "posted":
            raise UserError(_("The source invoice must be posted."))
        if self.invoice_id.company_id != self.company_id:
            raise UserError(_("The source invoice company does not match the return order."))
        if self.invoice_id.move_type != "out_invoice":
            raise UserError(_("Only customer invoices can be credited."))

        # Reuse source invoice commercial data; do not copy its payment
        # reconciliation. The credit note is posted as a separate document.
        vals = {
            "move_type": "out_refund",
            "partner_id": self.partner_id.id,
            "commercial_partner_id": self.partner_id.commercial_partner_id.id,
            "company_id": self.company_id.id,
            "currency_id": self.invoice_id.currency_id.id,
            "journal_id": self.invoice_id.journal_id.id,
            "invoice_origin": "%s / %s" % (self.invoice_id.name, self.picking_id.name),
            "ref": _("Return against %s") % self.invoice_id.name,
            "invoice_date": fields.Date.context_today(self),
            "invoice_line_ids": [(0, 0, {
                "product_id": self.product_id.id,
                "name": invoice_line.name,
                "quantity": self.qty_returned,
                "product_uom_id": invoice_line.product_uom_id.id,
                "price_unit": invoice_line.price_unit,
                "discount": invoice_line.discount,
                "account_id": invoice_line.account_id.id,
                "tax_ids": [(6, 0, invoice_line.tax_ids.ids)],
                "analytic_distribution": invoice_line.analytic_distribution,
            })],
        }
        credit_note = self.env["account.move"].create(vals)
        try:
            credit_note.action_post()
        except Exception as error:
            raise UserError(
                _("The credit note could not be posted. Check the accounting period, "
                  "journal, taxes, accounts, and lock dates. Original error: %s")
                % str(error)
            ) from error

        if self.invoice_id.payment_state == "paid" and self.payment_treatment == "refund":
            self._register_refund_payment(credit_note)
        return credit_note

    def _register_refund_payment(self, credit_note):
        self.ensure_one()
        if self.payment_journal_id.company_id != self.company_id:
            raise ValidationError(_("The refund journal belongs to another company."))
        if self.payment_journal_id.type not in ("cash", "bank"):
            raise ValidationError(_("Choose a cash or bank journal."))
        try:
            register = self.env["account.payment.register"].with_context(
                active_model="account.move",
                active_ids=credit_note.ids,
            ).create({
                "journal_id": self.payment_journal_id.id,
                "payment_date": fields.Date.context_today(self),
            })
            register.action_create_payments()
        except Exception as error:
            raise UserError(
                _("The credit note was posted, but Odoo could not register the refund. "
                  "Review the credit note and complete the refund manually. Error: %s")
                % str(error)
            ) from error

    def action_confirm_return(self):
        self.ensure_one()
        original_move = self._validate_selection()
        # Execute in a savepoint so a failed stock/accounting operation does
        # not leave partial records in the current transaction.
        try:
            with self.env.cr.savepoint():
                return_picking = self._create_stock_return(original_move)
                credit_note = self._create_credit_note()
                self.return_line_id.write({
                    "picking_id": return_picking.id,
                    "invoice_id": self.invoice_id.id,
                    "credit_note_id": credit_note.id,
                    "lot_id": self.lot_id.id,
                    "qty_returned": self.qty_returned,
                    "state": "processed",
                })
                self.return_line_id.order_id._update_state()
        except (UserError, ValidationError):
            raise
        except Exception as error:
            raise UserError(
                _("Return processing failed and changes were rolled back: %s") % str(error)
            ) from error
        return {"type": "ir.actions.act_window_close"}
