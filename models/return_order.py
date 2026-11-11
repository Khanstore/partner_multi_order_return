from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ReturnOrder(models.Model):
    _name = "return.order"
    _description = "Partner Multi Order Return"
    _order = "date desc, id desc"

    name = fields.Char(
        string="Reference", required=True, copy=False, readonly=True,
        default=lambda self: _("New"),
    )
    partner_id = fields.Many2one(
        "res.partner", string="Customer", required=True, index=True,
        domain=[("customer_rank", ">", 0)],
    )
    date = fields.Datetime(
        string="Return Date", required=True, default=fields.Datetime.now,
    )
    line_ids = fields.One2many(
        "return.order.line", "order_id", string="Return Lines",
        copy=True,
    )
    state = fields.Selection(
        [("draft", "Draft"), ("processed", "Processed")],
        default="draft", required=True,
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", store=True, readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = sequence.next_by_code("return.order") or _("New")
        return super().create(vals_list)

    def action_set_draft(self):
        for record in self:
            if any(line.state == "processed" for line in record.line_ids):
                raise UserError(
                    _("A return containing processed lines cannot be reset.")
                )
            record.state = "draft"

    def _update_state(self):
        for record in self:
            if record.line_ids and all(
                line.state == "processed" for line in record.line_ids
            ):
                record.state = "processed"
            else:
                record.state = "draft"


class ReturnOrderLine(models.Model):
    _name = "return.order.line"
    _description = "Partner Multi Order Return Line"
    _order = "id"

    order_id = fields.Many2one(
        "return.order", required=True, ondelete="cascade", index=True,
    )
    partner_id = fields.Many2one(
        related="order_id.partner_id", store=True, readonly=True,
    )
    product_id = fields.Many2one(
        "product.product", string="Product", required=True,
        domain=[("type", "in", ["consu", "product"])],
    )
    qty_returned = fields.Float(
        string="Quantity to Return", required=True,
        digits="Product Unit of Measure",
    )
    picking_id = fields.Many2one(
        "stock.picking", string="Return Picking", readonly=True, copy=False,
    )
    invoice_id = fields.Many2one(
        "account.move", string="Historical Invoice", readonly=True, copy=False,
    )
    credit_note_id = fields.Many2one(
        "account.move", string="Primary Credit Note", readonly=True, copy=False,
    )
    picking_ids = fields.Many2many(
        "stock.picking", "return_order_line_picking_rel",
        "return_line_id", "picking_id", string="Return Pickings",
        readonly=True, copy=False,
    )
    invoice_ids = fields.Many2many(
        "account.move", "return_order_line_invoice_rel",
        "return_line_id", "invoice_id", string="Historical Invoices",
        readonly=True, copy=False,
    )
    credit_note_ids = fields.Many2many(
        "account.move", "return_order_line_credit_rel",
        "return_line_id", "credit_note_id", string="Credit Notes",
        readonly=True, copy=False,
    )
    lot_id = fields.Many2one(
        "stock.lot", string="Primary Lot / Serial", readonly=True, copy=False,
    )
    lot_ids = fields.Many2many(
        "stock.lot", "return_order_line_lot_rel",
        "return_line_id", "lot_id", string="Lots / Serials",
        readonly=True, copy=False,
    )
    state = fields.Selection(
        [("draft", "Draft"), ("processed", "Processed")],
        default="draft", required=True, copy=False,
    )
    company_id = fields.Many2one(
        related="order_id.company_id", store=True, readonly=True,
    )

    @api.constrains("qty_returned", "product_id")
    def _check_quantity(self):
        for line in self:
            if line.qty_returned <= 0:
                raise ValidationError(_("Return quantity must be greater than zero."))
            if line.product_id and line.product_id.type == "service":
                raise ValidationError(_("Services cannot be returned to inventory."))

    @api.constrains(
        "state", "picking_id", "credit_note_id", "picking_ids", "credit_note_ids"
    )
    def _check_processed_documents(self):
        for line in self:
            if line.state == "processed" and (
                not line.picking_ids or not line.credit_note_ids
            ):
                raise ValidationError(
                    _("A processed line must have both a return picking and credit note.")
                )

    def action_open_return_pickings(self):
        """Open all stock return pickings linked to this return line."""
        self.ensure_one()
        if not self.picking_ids:
            raise UserError(_("There are no return pickings linked to this line."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Return Pickings"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("id", "in", self.picking_ids.ids)],
            "target": "current",
        }

    def action_open_credit_notes(self):
        """Open all credit notes linked to this return line."""
        self.ensure_one()
        if not self.credit_note_ids:
            raise UserError(_("There are no credit notes linked to this line."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Credit Notes"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.credit_note_ids.ids)],
            "target": "current",
        }

    def action_open_history_wizard(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("This return line has already been processed."))
        if not self.order_id.partner_id:
            raise UserError(_("Select a customer on the return order first."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Select Historical Delivery and Invoice"),
            "res_model": "return.order.history.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_return_line_id": self.id,
                "default_partner_id": self.order_id.partner_id.id,
                "default_product_id": self.product_id.id,
                "default_qty_returned": self.qty_returned,
            },
        }


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Store the setting in ir.config_parameter. This avoids adding a physical
    # column to res_company during module installation/upgrade.
    return_allocation_method = fields.Selection(
        [("fifo", "FIFO"), ("lifo", "LIFO"), ("manual", "Manual")],
        string="Return Allocation Method",
        default="manual",
        config_parameter="partner_multi_order_return.return_allocation_method",
    )
