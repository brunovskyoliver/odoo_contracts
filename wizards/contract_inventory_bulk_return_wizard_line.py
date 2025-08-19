# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ContractInventoryBulkReturnWizardLine(models.TransientModel):
    _name = 'contract.inventory.bulk.return.wizard.line'
    _description = 'Return Inventory Lines Selection'

    wizard_id = fields.Many2one(
        comodel_name='contract.inventory.bulk.return.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    inventory_line_id = fields.Many2one(
        'contract.inventory.line',
        string='Inventory Line',
        required=True,
    )
    product_id = fields.Many2one(
        related='inventory_line_id.product_id',
        string='Product',
        readonly=True,
    )
    available_qty = fields.Float(
        related='inventory_line_id.quantity',
        string='Available Quantity',
        readonly=True,
    )
    return_qty = fields.Float(
        string='Quantity to Return',
        required=True,
    )
    uom_id = fields.Many2one(
        related='inventory_line_id.uom_id',
        string='Unit of Measure',
        readonly=True,
    )

    @api.onchange('inventory_line_id')
    def _onchange_inventory_line_id(self):
        if self.inventory_line_id:
            self.return_qty = self.inventory_line_id.quantity

    @api.constrains('return_qty', 'available_qty')
    def _check_return_qty(self):
        for record in self:
            if record.return_qty <= 0:
                raise ValidationError(_('Return quantity must be positive'))
            if record.return_qty > record.available_qty:
                raise ValidationError(_('Cannot return more than available quantity'))
