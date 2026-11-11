from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class PurchaseReturnOrder(models.Model):
    _name = "purchase.return.order"
    _description = "Purchase Return"
    _order = "date desc, id desc"

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _("New"))
    partner_id = fields.Many2one("res.partner", string="Vendor", required=True, index=True,
                                 domain=[("supplier_rank", ">", 0)])
    date = fields.Datetime(required=True, default=fields.Datetime.now)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    line_ids = fields.One2many("purchase.return.order.line", "order_id", string="Return Lines", copy=True)
    state = fields.Selection([("draft", "Draft"), ("processed", "Processed")], default="draft", required=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("purchase.return.order") or _("New")
        return super().create(vals_list)

    def _update_state(self):
        for order in self:
            order.state = "processed" if order.line_ids and all(x.state == "processed" for x in order.line_ids) else "draft"


class PurchaseReturnOrderLine(models.Model):
    _name = "purchase.return.order.line"
    _description = "Purchase Return Line"
    _order = "id"

    order_id = fields.Many2one("purchase.return.order", required=True, ondelete="cascade", index=True)
    partner_id = fields.Many2one(related="order_id.partner_id", store=True, readonly=True)
    company_id = fields.Many2one(related="order_id.company_id", store=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True, domain=[("type", "in", ["consu", "product"])])
    qty_returned = fields.Float(string="Quantity to Return", required=True, digits="Product Unit of Measure")
    state = fields.Selection([("draft", "Draft"), ("processed", "Processed")], default="draft", required=True, copy=False)
    picking_ids = fields.Many2many("stock.picking", "purchase_return_line_picking_rel", "return_line_id", "picking_id", readonly=True, copy=False)
    bill_ids = fields.Many2many("account.move", "purchase_return_line_bill_rel", "return_line_id", "bill_id", string="Vendor Bills", readonly=True, copy=False)
    refund_ids = fields.Many2many("account.move", "purchase_return_line_refund_rel", "return_line_id", "refund_id", string="Vendor Credit Notes", readonly=True, copy=False)
    lot_ids = fields.Many2many("stock.lot", "purchase_return_line_lot_rel", "return_line_id", "lot_id", readonly=True, copy=False)

    @api.constrains("qty_returned", "product_id")
    def _check_qty(self):
        for line in self:
            if line.qty_returned <= 0:
                raise ValidationError(_("Return quantity must be greater than zero."))
            if line.product_id.type == "service":
                raise ValidationError(_("Services cannot be returned to inventory."))

    @api.constrains("state", "picking_ids", "refund_ids")
    def _check_documents(self):
        for line in self:
            if line.state == "processed" and (not line.picking_ids or not line.refund_ids):
                raise ValidationError(_("A processed purchase return must have a stock return and vendor credit note."))

    def action_open_history_wizard(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("This return line has already been processed."))
        return {"type": "ir.actions.act_window", "name": _("Select Historical Receipt and Vendor Bill"),
                "res_model": "purchase.return.history.wizard", "view_mode": "form", "target": "new",
                "context": {"default_return_line_id": self.id, "default_partner_id": self.order_id.partner_id.id,
                            "default_product_id": self.product_id.id, "default_qty_returned": self.qty_returned}}

    def action_open_pickings(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Supplier Return Pickings"), "res_model": "stock.picking",
                "view_mode": "list,form", "domain": [("id", "in", self.picking_ids.ids)]}

    def action_open_refunds(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Vendor Credit Notes"), "res_model": "account.move",
                "view_mode": "list,form", "domain": [("id", "in", self.refund_ids.ids)]}
