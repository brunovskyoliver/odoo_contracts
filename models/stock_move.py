# Copyright 2025 Novem IT
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    invoice_line_id = fields.Many2one(
        'account.move.line',
        string='Invoice Line',
        readonly=True,
        ondelete='set null',
        index=True,
    )

    contract_inventory_line_id = fields.Many2one(
        'contract.inventory.line',
        string='Contract Inventory Line',
        ondelete='set null',
        index=True,
    )
