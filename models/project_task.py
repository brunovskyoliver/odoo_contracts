# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

class ProjectTask(models.Model):
    _inherit = 'project.task'

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
                else:
                    _logger.info('Task %s - Stage is not "done" (%s), skipping inventory update', 
                               task.id, stage.name)
        
        return result

    def _update_contract_inventory(self):
        """Update contract inventory when task is marked as done."""
        _logger.info('Starting contract inventory update for task %s', self.id)
        
        if not self.sale_order_id:
            _logger.warning('Task %s has no sale_order_id, skipping inventory update', self.id)
            return
        
        if not self.partner_id:
            _logger.warning('Task %s has no partner_id, skipping inventory update', self.id)
            return

        _logger.info('Task %s - Found sale order: %s, partner: %s', 
                    self.id, self.sale_order_id.id, self.partner_id.id)

        # Get the company partner - either directly or through parent
        company_partner = self.partner_id
        if not company_partner.is_company and company_partner.parent_id:
            company_partner = company_partner.parent_id
            _logger.info('Task %s - Using parent company: %s instead of contact: %s', 
                        self.id, company_partner.id, self.partner_id.id)

        # Only proceed if we found a company
        if not company_partner.is_company:
            _logger.warning('Task %s - No company found for partner %s', 
                          self.id, self.partner_id.id)
            return

        # Find contract inventory for the company
        contract_inventory = self.env['contract.inventory'].search([
            ('partner_id', '=', company_partner.id),
            ('active', '=', True)
        ], limit=1)

        if not contract_inventory:
            _logger.warning('Task %s - No active contract inventory found for company %s', 
                          self.id, company_partner.id)
            return

        _logger.info('Task %s - Found contract inventory: %s', 
                    self.id, contract_inventory.id)

        # Get all sale order lines with products and quantities
        sale_order = self.sale_order_id
        if sale_order and sale_order.state == 'sale':  # Only process confirmed sales orders
            _logger.info('Task %s - Processing sale order: %s with %s lines', 
                        self.id, sale_order.id, len(sale_order.order_line))
            
            for line in sale_order.order_line:
                _logger.info('Task %s - Processing order line: %s, Product: %s, Quantity: %s', 
                           self.id, line.id, 
                           line.product_id.id if line.product_id else 'No product',
                           line.product_uom_qty)
                
                if line.product_id and line.product_uom_qty > 0:
                    # Check if product already exists in inventory
                    inventory_line = self.env['contract.inventory.line'].search([
                        ('inventory_id', '=', contract_inventory.id),
                        ('product_id', '=', line.product_id.id)
                    ], limit=1)

                    try:
                        InventoryLine = self.env['contract.inventory.line'].with_context(no_stock_movement=True)
                        if inventory_line:
                            # Update existing line (tracking only, no stock movement)
                            new_qty = inventory_line.quantity + line.product_uom_qty
                            _logger.info('Task %s - Updating existing inventory line %s. Old qty: %s, Adding: %s, New qty: %s', 
                                       self.id, inventory_line.id, inventory_line.quantity, 
                                       line.product_uom_qty, new_qty)
                            inventory_line.write({
                                'quantity': new_qty
                            })
                        else:
                            # Create new inventory line (tracking only, no stock movement)
                            _logger.info('Task %s - Creating new inventory line for product %s with qty %s', 
                                       self.id, line.product_id.id, line.product_uom_qty)
                            InventoryLine.create({
                                'inventory_id': contract_inventory.id,
                                'product_id': line.product_id.id,
                                'quantity': line.product_uom_qty,
                                'state': 'assigned'
                            })
                    except Exception as e:
                        _logger.error('Task %s - Error while processing inventory line: %s', 
                                    self.id, str(e), exc_info=True)
