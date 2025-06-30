# Copyright 2025 NOVEM IT
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    stock_move_ids = fields.One2many(
        'stock.move',
        'invoice_line_id',
        string='Stock Moves',
        copy=False,
        readonly=True,
    )
