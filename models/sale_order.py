# Copyright 2016 Tecnativa - Carlos Dauden
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, api, fields
from odoo.tools import float_compare

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        # First call the original action_confirm
        res = super().action_confirm()
        
        # After confirmation, get the pickings and validate them
        for order in self:
            pickings = order.picking_ids.filtered(lambda p: p.state not in ['done', 'cancel'])
            for picking in pickings:
                picking.action_assign()
                picking.button_validate()
        
        return res

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Source Warehouse',
        help='Specific warehouse to source this product from. If not set, will use the warehouse set on the sale order.'
    )

    # def _prepare_procurement_group_vals(self):
    #     vals = super()._prepare_procurement_group_vals()
    #     if self.warehouse_id:
    #         vals['warehouse_id'] = self.warehouse_id.id
    #     return vals

    @api.onchange('warehouse_id')
    def _onchange_warehouse_id(self):
        if self.warehouse_id and self.product_id:
            # Get product availability in selected warehouse
            quant = self.env['stock.quant'].search([
                ('product_id', '=', self.product_id.id),
                ('location_id', '=', self.warehouse_id.lot_stock_id.id),
            ], limit=1)
            if quant:
                self.product_uom_qty = min(self.product_uom_qty, quant.available_quantity)

    def _prepare_procurement_values(self, group_id=False):
        values = super()._prepare_procurement_values(group_id=group_id)
        if self.warehouse_id:
            values['warehouse_id'] = self.warehouse_id
        return values
