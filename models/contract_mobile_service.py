# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _


class ContractMobileService(models.Model):
    _name = "contract.mobile.service"
    _description = "Mobilná služba"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Názov", required=True, tracking=True)
    phone_number = fields.Char(string="Telefónne číslo", required=True, tracking=True)
    operator = fields.Selection(
        selection=[
            ('telekom', 'Telekom'),
            ('o2', 'O2'),
        ],
        string="Operátor",
        required=True,
        tracking=True,
    )
    is_active = fields.Boolean(
        string="Je aktívna",
        default=True,
        tracking=True,
        help="Iba aktívne mobilné služby budú fakturované",
    )
    inventory_id = fields.Many2one(
        comodel_name="contract.inventory",
        string="Inventár",
        required=True,
        tracking=True,
    )
    contract_line_id = fields.Many2one(
        comodel_name="contract.line",
        string="Riadok zmluvy",
        tracking=True,
    )
    contract_id = fields.Many2one(
        related="contract_line_id.contract_id",
        string="Zmluva",
        store=True,
    )
    partner_id = fields.Many2one(
        related="contract_id.partner_id",
        string="Partner",
        store=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Spoločnosť",
        default=lambda self: self.env.company,
        tracking=True,
    )
    notes = fields.Text(string="Poznámky")

    def write(self, vals):
        """Aktualizovať názov riadku zmluvy, keď sa zmení telefónne číslo alebo stav aktivity"""
        result = super().write(vals)
        if 'phone_number' in vals or 'is_active' in vals:
            contract_lines = self.mapped('contract_line_id')
            for contract_line in contract_lines:
                if contract_line and contract_line.is_mobile_service:
                    contract_line.with_context(from_mobile_service_update=True)._compute_mobile_service_description()
        return result

    @api.model
    def create(self, vals):
        """Aktualizovať názov riadku zmluvy pri vytvorení novej mobilnej služby"""
        record = super().create(vals)
        if record.contract_line_id and record.contract_line_id.is_mobile_service:
            record.contract_line_id.with_context(from_mobile_service_update=True)._compute_mobile_service_description()
        return record
