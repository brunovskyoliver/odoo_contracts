# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ContractInventoryBulkReturnWizard(models.TransientModel):
    _name = 'contract.inventory.bulk.return.wizard'
    _description = 'Vrátiť viacero inventárnych riadkov do skladu'

    inventory_id = fields.Many2one(
        'contract.inventory',
        string='Inventár',
        required=True,
        readonly=True,
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Vrátiť do skladu',
        required=True,
        help='Vyberte sklad, do ktorého majú byť položky vrátené',
    )
    line_ids = fields.One2many(
        comodel_name='contract.inventory.bulk.return.wizard.line',
        inverse_name='wizard_id',
        string='Riadky na vrátenie',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self._context.get('active_model') == 'contract.inventory' and self._context.get('active_id'):
            inventory = self.env['contract.inventory'].browse(self._context.get('active_id'))
            res.update({
                'inventory_id': inventory.id,
                'warehouse_id': inventory.warehouse_id.id if inventory.warehouse_id else False,
            })
        return res

    def action_add_all_lines(self):
        self.ensure_one()
        inventory_lines = self.env['contract.inventory.line'].search([
            ('inventory_id', '=', self.inventory_id.id)
        ])
        for inv_line in inventory_lines:
            self.env['contract.inventory.bulk.return.wizard.line'].create({
                'wizard_id': self.id,
                'inventory_line_id': inv_line.id,
                'return_qty': inv_line.quantity,
            })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'contract.inventory.bulk.return.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new'
        }

    def action_return_to_warehouse(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_('Vyberte aspoň jeden produkt na vrátenie'))

        # Create one picking for all returns
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse_id.in_type_id.id,
            'location_id': self.env.ref('stock.stock_location_customers').id,
            'location_dest_id': self.warehouse_id.lot_stock_id.id,
            'partner_id': self.inventory_id.partner_id.id,
            'origin': f'Hromadné vrátenie: {self.inventory_id.name}',
            'company_id': self.inventory_id.company_id.id or self.env.company.id,
        })

        for wizard_line in self.line_ids:
            if wizard_line.return_qty <= 0:
                continue

            # Create stock move
            move = self.env['stock.move'].create({
                'name': f'Vrátenie: {wizard_line.product_id.name}',
                'product_id': wizard_line.product_id.id,
                'product_uom_qty': wizard_line.return_qty,
                'product_uom': wizard_line.uom_id.id,
                'picking_id': picking.id,
                'location_id': self.env.ref('stock.stock_location_customers').id,
                'location_dest_id': self.warehouse_id.lot_stock_id.id,
                'company_id': picking.company_id.id,
                'picking_type_id': self.warehouse_id.in_type_id.id,
                'state': 'assigned',  # Mark as available immediately since this is a return
            })

            # Create move line with immediate validation
            self.env['stock.move.line'].create({
                'move_id': move.id,
                'picking_id': picking.id,
                'product_id': move.product_id.id,
                'product_uom_id': move.product_uom.id,
                'qty_done': wizard_line.return_qty,
                'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id,
                'state': 'assigned',
            })

            # If returning full quantity, delete the inventory line
            if wizard_line.return_qty >= wizard_line.available_qty:
                wizard_line.inventory_line_id.unlink()
            else:
                # Otherwise, reduce the quantity
                wizard_line.inventory_line_id.write({
                    'quantity': wizard_line.available_qty - wizard_line.return_qty
                })

        # Process and validate the picking
        picking.action_confirm()
        picking.with_context(skip_backorder=True).button_validate()
        return {'type': 'ir.actions.act_window_close'}
        # Show the picking
        return {
            'type': 'ir.actions.act_window',
            'name': _('Vrátené príjemky'),
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
            'view_type': 'form',
            'target': 'current',
        }
