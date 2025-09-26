# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class ProjectTask(models.Model):
    _inherit = 'project.task'

    was_done = fields.Boolean(string='Was Done', default=False)

    def _return_materials_to_warehouse(self, record):
        """Handle returning materials from contract inventory to warehouse."""
        if not record.was_done or not record.sudo().sale_order_id:
            return

        try:
            search_partner = record.partner_id
            if not search_partner:
                _logger.warning('No partner found for task %s', record.id)
                return

            # If partner is a contact with a parent company, use the parent
            if not search_partner.is_company and search_partner.parent_id:
                search_partner = search_partner.parent_id
                _logger.info('Task %s - Using parent company: %s instead of contact: %s', 
                            record.id, search_partner.id, record.partner_id.id)

            contract_inventory = self.env['contract.inventory'].sudo().search([
                ('partner_id', '=', search_partner.id),
                ('active', '=', True)
            ], limit=1)

            if not contract_inventory:
                _logger.warning('No active contract inventory found for partner %s', search_partner.id)
                return

            warehouse = self.env['stock.warehouse'].sudo().search([], limit=1)
            if not warehouse:
                _logger.warning('No warehouse found')
                return

            # Create one picking for all returns
            picking = self.env['stock.picking'].sudo().create({
                'picking_type_id': warehouse.in_type_id.id,
                'location_id': self.env.ref('stock.stock_location_customers').id,
                'location_dest_id': warehouse.lot_stock_id.id,
                'partner_id': search_partner.id,
                'origin': f'Return from Task {record.name}',
                'company_id': contract_inventory.company_id.id or self.env.company.id,
            })

            has_moves = False
            
            for line in record.sudo().sale_order_id.order_line:
                if not line.product_id or line.product_uom_qty <= 0:
                    continue

                inventory_line = self.env['contract.inventory.line'].sudo().search([
                    ('inventory_id', '=', contract_inventory.id),
                    ('product_id', '=', line.product_id.id)
                ], limit=1)

                if not inventory_line or inventory_line.quantity < line.product_uom_qty:
                    continue

                # Create stock move
                move = self.env['stock.move'].sudo().create({
                    'name': f'Return: {line.product_id.name}',
                    'product_id': line.product_id.id,
                    'product_uom_qty': line.product_uom_qty,
                    'product_uom': line.product_id.uom_id.id,
                    'picking_id': picking.id,
                    'location_id': picking.location_id.id,
                    'location_dest_id': picking.location_dest_id.id,
                    'company_id': picking.company_id.id,
                    'picking_type_id': warehouse.in_type_id.id,
                    'state': 'draft',
                })

                # Create move line with done quantity
                self.env['stock.move.line'].sudo().create({
                    'move_id': move.id,
                    'picking_id': picking.id,
                    'product_id': line.product_id.id,
                    'product_uom_id': line.product_id.uom_id.id,
                    'qty_done': line.product_uom_qty,
                    'location_id': picking.location_id.id,
                    'location_dest_id': picking.location_dest_id.id,
                })

                has_moves = True

                # Update inventory line quantity
                new_qty = inventory_line.quantity - line.product_uom_qty
                if new_qty > 0:
                    inventory_line.sudo().write({
                        'quantity': new_qty
                    })
                else:
                    inventory_line.sudo().unlink()

            if has_moves:
                # Process the picking
                picking.action_confirm()
                # Force availability since this is a return
                picking.action_assign()
                # Validate the picking with immediate transfer
                picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
                _logger.info('Successfully processed return picking %s for task %s', picking.id, record.id)

        except Exception as e:
            _logger.error('Error processing return for task %s: %s', record.id, str(e))
            raise UserError(_('Error returning materials to warehouse: %s') % str(e))

    def unlink(self):
        """Override unlink to handle material returns before deletion."""
        for record in self:
            self._return_materials_to_warehouse(record)
        return super(ProjectTask, self).unlink()

    def write(self, vals):
        result = super(ProjectTask, self).write(vals)
        
        # Check if stage changed to 'done'
        if 'stage_id' in vals:
            _logger.info('Stage changed for task(s): %s', self.ids)
            for task in self:
                stage = task.stage_id
                _logger.info('Task %s - Current stage name: %s', task.id, stage.name)
                if stage.name.lower() == 'done':
                    _logger.info('Task %s marked as done, updating contract inventory', task.id)
                    task._update_contract_inventory()
                    task.was_done = True
                else:
                    _logger.info('Task %s - Stage is not "done" (%s), skipping inventory update', 
                               task.id, stage.name)
        
        return result

    def _update_contract_inventory(self):
        """Update contract inventory when task is marked as done."""
        _logger.info('Starting contract inventory update for task %s', self.id)

        # Basic guards
        sale_order = self.sudo().sale_order_id
        if not sale_order:
            _logger.warning('Task %s has no sale_order_id, skipping inventory update', self.id)
            return
        if not self.partner_id:
            _logger.warning('Task %s has no partner_id, skipping inventory update', self.id)
            return

        partner = self.partner_id
        _logger.info('Task %s - Found sale order: %s, partner: %s',
                    self.id, sale_order.id, partner.id)

        # Choose which partner to use for contract inventory
        if partner.is_company:
            search_partner = partner
        elif partner.parent_id:
            search_partner = partner.parent_id
            _logger.info('Task %s - Using parent company %s instead of contact %s',
                        self.id, search_partner.id, partner.id)
        else:
            search_partner = partner
            _logger.info('Task %s - No company/parent; using partner %s directly',
                        self.id, partner.id)

        # Find active contract inventory for that partner
        contract_inventory = self.env['contract.inventory'].sudo().search([
            ('partner_id', '=', search_partner.id),
            ('active', '=', True),
        ], limit=1)

        if not contract_inventory:
            _logger.warning('Task %s - No active contract inventory found for partner %s',
                            self.id, search_partner.id)
            return

        _logger.info('Task %s - Found contract inventory: %s',
                    self.id, contract_inventory.id)

        # Only process confirmed sales orders
        if sale_order.sudo().state != 'sale':
            _logger.info('Task %s - Sale order %s not in state "sale" (is: %s); skipping.',
                        self.id, sale_order.id, sale_order.state)
            return

        _logger.info('Task %s - Processing sale order: %s with %s lines',
                    self.id, sale_order.id, len(sale_order.order_line))

        InventoryLine = self.env['contract.inventory.line'].sudo().with_context(no_stock_movement=True)

        for line in sale_order.order_line:
            _logger.info('Task %s - Processing order line: %s, Product: %s, Quantity: %s',
                        self.id, line.id,
                        line.product_id.id if line.product_id else 'No product',
                        line.product_uom_qty)

            if not line.product_id or line.product_uom_qty <= 0:
                continue

            inventory_line = self.env['contract.inventory.line'].sudo().search([
                ('inventory_id', '=', contract_inventory.id),
                ('product_id', '=', line.product_id.id),
            ], limit=1)

            try:
                # Check available quantity in warehouse
                available_qty = line.product_id.with_context(warehouse=self.env['stock.warehouse'].sudo().search([], limit=1).id).qty_available
                if available_qty < line.product_uom_qty:
                    raise UserError(_(
                        'Nie je možné priradiť väčšie množstvo, než je dostupné na sklade. '
                        'Produkt %s má k dispozícii iba %s jednotiek, požadované: %s.'
                    ) % (line.product_id.name, available_qty, line.product_uom_qty))

                if inventory_line:
                    new_qty = inventory_line.quantity + line.product_uom_qty
                    _logger.info(
                        'Task %s - Updating inventory line %s. Old qty: %s, +%s => %s (Available in warehouse: %s)',
                        self.id, inventory_line.id, inventory_line.quantity,
                        line.product_uom_qty, new_qty, available_qty
                    )
                    inventory_line.with_context(no_stock_movement=True).write({'quantity': new_qty})
                else:
                    _logger.info(
                        'Task %s - Creating inventory line for product %s with qty %s (Available in warehouse: %s)',
                        self.id, line.product_id.id, line.product_uom_qty, available_qty
                    )
                    InventoryLine.with_context(no_stock_movement=True).create({
                        'inventory_id': contract_inventory.id,
                        'product_id': line.product_id.id,
                        'quantity': line.product_uom_qty,
                        'state': 'assigned',
                    })
            except Exception as e:
                _logger.error('Task %s - Error while processing inventory line: %s',
                            self.id, str(e), exc_info=True)
                raise UserError(_('Error updating contract inventory: %s') % str(e))
