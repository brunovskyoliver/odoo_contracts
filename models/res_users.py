# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

class ResUsers(models.Model):
    _inherit = 'res.users'

    contract_inventory_only = fields.Boolean(
        string='View Only Contract Inventory',
        default=False,
        help='If checked, the user will only see inventory items related to their contracts'
    )

    def write(self, vals):
        res = super().write(vals)
        if 'contract_inventory_only' in vals:
            group = self.env.ref('contract.group_contract_inventory_only')
            for user in self:
                if user.contract_inventory_only:
                    user.sudo().write({'groups_id': [(4, group.id, False)]})
                else:
                    user.sudo().write({'groups_id': [(3, group.id, False)]})
        return res