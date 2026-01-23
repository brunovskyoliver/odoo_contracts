# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import date


class ContractInventoryCreateRentalWizard(models.TransientModel):
    _name = 'contract.inventory.create.rental.wizard'
    _description = 'Vytvoriť prenájom z inventárnych riadkov'

    inventory_id = fields.Many2one(
        'contract.inventory',
        string='Inventár',
        required=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Partner',
        related='inventory_id.partner_id',
        readonly=True,
    )
    contract_id = fields.Many2one(
        'contract.contract',
        string='Existujúca zmluva prenájmu',
        domain="[('partner_id', '=', partner_id), ('x_contract_type', '=', 'Prenájom')]",
        help='Vyberte existujúcu zmluvu prenájmu alebo nechajte prázdne pre vytvorenie novej',
    )
    create_new_contract = fields.Boolean(
        string='Vytvoriť novú zmluvu',
        default=False,
        help='Ak je zaškrtnuté, vytvorí sa nová zmluva prenájmu',
    )
    line_ids = fields.Many2many(
        comodel_name='contract.inventory.line',
        relation='contract_inventory_rental_wizard_line_rel',
        column1='wizard_id',
        column2='line_id',
        string='Inventárne riadky',
    )
    date_start = fields.Date(
        string='Dátum začiatku',
        default=fields.Date.context_today,
        required=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self._context.get('active_model') == 'contract.inventory' and self._context.get('active_id'):
            inventory = self.env['contract.inventory'].browse(self._context.get('active_id'))
            res.update({
                'inventory_id': inventory.id,
            })
            # Check for existing rental contract for this partner
            existing_contract = self.env['contract.contract'].search([
                ('partner_id', '=', inventory.partner_id.id),
                ('x_contract_type', '=', 'Prenájom'),
                ('active', '=', True),
            ], limit=1)
            if existing_contract:
                res['contract_id'] = existing_contract.id
        return res

    @api.onchange('create_new_contract')
    def _onchange_create_new_contract(self):
        if self.create_new_contract:
            self.contract_id = False

    def action_create_rental_lines(self):
        """Vytvoriť riadky zmluvy prenájmu z vybraných inventárnych riadkov"""
        self.ensure_one()
        
        if not self.line_ids:
            raise UserError(_('Vyberte aspoň jeden inventárny riadok'))

        if not self.partner_id:
            raise UserError(_('Partner nie je nastavený na inventári'))

        # Get or create rental contract
        if self.create_new_contract or not self.contract_id:
            contract = self._create_rental_contract()
        else:
            contract = self.contract_id

        # Create contract lines for each selected inventory line
        created_lines = self.env['contract.line']
        for inv_line in self.line_ids:
            contract_line = self._create_contract_line(contract, inv_line)
            created_lines += contract_line
            
            # Link inventory line to contract line
            inv_line.write({
                'contract_line_id': contract_line.id,
                'state': 'assigned',
            })

        from odoo.tools.safe_eval import safe_eval

        action = self.env['ir.actions.act_window']._for_xml_id('contract.action_customer_contract')

        action_ctx = action.get('context') or {}
        if isinstance(action_ctx, str):
            action_ctx = safe_eval(action_ctx)

        # Add highlighted line IDs to context for decoration
        ctx = dict(self.env.context, **action_ctx)
        ctx['highlighted_line_ids'] = created_lines.ids

        action.update({
            'res_id': contract.id,
            'view_mode': 'form',
            'views': [(self.env.ref('contract.contract_contract_customer_form_view').id, 'form')],
            'target': 'current',
            'context': ctx,
        })
        return action



    def _create_rental_contract(self):
        """Vytvoriť novú zmluvu prenájmu"""
        return self.env['contract.contract'].create({
            'name': _('Prenájom - %s') % self.partner_id.name,
            'partner_id': self.partner_id.id,
            'x_contract_type': 'Prenájom',
            'contract_type': 'sale',
            'date_start': self.date_start,
            'company_id': self.inventory_id.company_id.id or self.env.company.id,
            'inventory_id': self.inventory_id.id,
        })

    def _create_contract_line(self, contract, inv_line):
        """Vytvoriť riadok zmluvy z inventárneho riadku"""
        return self.env['contract.line'].create({
            'contract_id': contract.id,
            'product_id': inv_line.product_id.id,
            'name': inv_line.product_id.name,
            'quantity': inv_line.quantity,
            'uom_id': inv_line.uom_id.id,
            'price_unit': inv_line.product_id.lst_price,
            'date_start': self.date_start,
            'recurring_next_date': contract.recurring_next_date or self.date_start,
            'in_inventory': True,
            'recurring_rule_type': 'monthly',
            'recurring_interval': 1,
        })

