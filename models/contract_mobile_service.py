# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


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
    ignore_alert = fields.Boolean(
        string="Ignorovať upozornenia",
        default=False,
        tracking=True,
        help="Ak je zaškrtnuté, nebudú sa zobrazovať upozornenia o zle nastavenom paušále",
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

    def _validate_phone_number(self, phone_number):
        """Validate and format phone number"""
        if not phone_number:
            return phone_number
            
        # Remove any spaces, dashes, or other separators
        cleaned_number = ''.join(filter(str.isdigit, phone_number))
        
        # If it's already in correct format with 421 prefix
        if cleaned_number.startswith('421') and len(cleaned_number) == 12:
            return cleaned_number
            
        # If number starts with 0, remove it
        if cleaned_number.startswith('0'):
            cleaned_number = cleaned_number[1:]
        
        # Check if we have exactly 9 digits after removing prefix
        if len(cleaned_number) != 9:
            raise ValidationError(_('Chybne telefonne cislo, musi mat presne 9 cifier bez prefixu alebo 12 cifier (s 421). Ziskane: %s') % phone_number)
            
        return '421' + cleaned_number

    def write(self, vals):
        """Aktualizovať názov riadku zmluvy, keď sa zmení telefónne číslo alebo stav aktivity"""
        if 'phone_number' in vals:
            vals['phone_number'] = self._validate_phone_number(vals['phone_number'])
            
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
        if 'phone_number' in vals:
            vals['phone_number'] = self._validate_phone_number(vals['phone_number'])
            
        record = super().create(vals)
        if record.contract_line_id and record.contract_line_id.is_mobile_service:
            record.contract_line_id.with_context(from_mobile_service_update=True)._compute_mobile_service_description()
        return record
