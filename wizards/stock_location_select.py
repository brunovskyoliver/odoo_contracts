from odoo import fields, models, api, _
from odoo.exceptions import UserError

class StockLocationSelectWizard(models.TransientModel):
    _name = 'stock.location.select.wizard'
    _description = 'Storage Location Selection'

    invoice_id = fields.Many2one('account.move', required=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Storage Location', required=True)

    def action_confirm(self):
        self.ensure_one()
        return self.invoice_id.with_context(selected_warehouse_id=self.warehouse_id.id).create_stock_moves()
