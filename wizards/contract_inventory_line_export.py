# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

class ContractInventoryLineExport(models.TransientModel):
    _name = 'contract.inventory.line.export.wizard'
    _description = 'Export Contract Inventory Lines'

    inventory_line_ids = fields.Many2many(
        'contract.inventory.line',
        'contract_inv_line_export_rel',  # custom shorter relation table name
        'wizard_id',
        'line_id',
        string='Inventory Lines'
    )

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        
        # Get the active model and IDs
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids', [])
        
        if active_model == 'contract.inventory':
            # Get inventory lines from the inventory record
            inventory = self.env['contract.inventory'].browse(active_ids[0])
            defaults['inventory_line_ids'] = [(6, 0, inventory.inventory_line_ids.ids)]
        elif active_model == 'contract.inventory.line':
            # Direct selection of inventory lines
            defaults['inventory_line_ids'] = [(6, 0, active_ids)]
        else:
            # Get from context (e.g., one2many field selection)
            inventory_line_ids = self.env.context.get('default_inventory_line_ids')
            if inventory_line_ids:
                if isinstance(inventory_line_ids, (list, tuple)) and inventory_line_ids:
                    # Handle command tuples
                    if isinstance(inventory_line_ids[0], (list, tuple)):
                        ids = inventory_line_ids[0][2]  # Get IDs from (6, 0, [ids]) format
                    else:
                        ids = inventory_line_ids
                    defaults['inventory_line_ids'] = [(6, 0, ids)]
            
        return defaults

    def action_export_pdf(self):
        self.ensure_one()
        return self.env.ref('contract.report_contract_inventory_lines_action').report_action(self.inventory_line_ids)