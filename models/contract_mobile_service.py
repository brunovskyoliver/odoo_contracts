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
        
        redirect_action = None
        
        # Ak sa mení inventory_id, spracovať zmenu
        if 'inventory_id' in vals:
            for record in self:
                old_inventory_id = record.inventory_id.id
                new_inventory_id = vals.get('inventory_id')
                # Ak sa inventory_id zmenilo
                if old_inventory_id != new_inventory_id:
                    # Uložíme starý contract_line na vymazanie
                    contract_line_to_delete = record.contract_line_id
                    
                    # Nájdeme nový inventár a jeho partnera
                    new_inventory = self.env['contract.inventory'].browse(new_inventory_id)
                    if new_inventory and new_inventory.partner_id:
                        # Hľadáme Mobilky zmluvu pre partnera nového inventára
                        mobilky_contract = self.env['contract.contract'].search([
                            ('partner_id', '=', new_inventory.partner_id.id),
                            ('x_contract_type', '=', 'Mobilky'),
                            ('inventory_id', '=', new_inventory_id),
                        ], limit=1)
                        
                        if mobilky_contract:
                            # Nájdeme produkt podľa názvu mobilnej služby (name)
                            product_id = False
                            if contract_line_to_delete and contract_line_to_delete.product_id:
                                product_id = contract_line_to_delete.product_id.id
                            else:
                                # Hľadáme produkt podľa názvu mobilnej služby
                                product = self.env['product.product'].search([
                                    ('name', '=', record.name)
                                ], limit=1)
                                if product:
                                    product_id = product.id
                            
                            # Vytvoríme nový riadok zmluvy
                            new_contract_line = self.env['contract.line'].create({
                                'contract_id': mobilky_contract.id,
                                'name': f"{record.name}: {record.phone_number}",
                                'product_id': product_id,
                                'quantity': 1,
                                'price_unit': contract_line_to_delete.price_unit if contract_line_to_delete else 0,
                                'uom_id': contract_line_to_delete.uom_id.id if contract_line_to_delete else False,
                                'is_mobile_service': True,
                                'date_start': fields.Date.today(),
                                'recurring_rule_type': 'monthly',
                                'recurring_interval': 1,
                                'recurring_next_date': mobilky_contract.recurring_next_date or fields.Date.today(),
                            })
                            
                            # Nastavíme nový contract_line_id na mobilnú službu
                            vals['contract_line_id'] = new_contract_line.id
                            
                            # Pripravíme redirect akciu na novú zmluvu
                            redirect_action = {
                                'type': 'ir.actions.act_window',
                                'res_model': 'contract.contract',
                                'res_id': mobilky_contract.id,
                                'view_mode': 'form',
                                'target': 'current',
                            }
                    
                    # Odpojíme starú mobilnú službu od starého riadku zmluvy
                    if contract_line_to_delete:
                        record.contract_line_id = False
                        # Vymažeme starý riadok zmluvy
                        contract_line_to_delete.unlink()
            
        result = super().write(vals)
        
        if 'phone_number' in vals or 'is_active' in vals:
            contract_lines = self.mapped('contract_line_id')
            for contract_line in contract_lines:
                if contract_line and contract_line.is_mobile_service:
                    contract_line.with_context(from_mobile_service_update=True)._compute_mobile_service_description()
        
        # Ak máme redirect akciu, vrátime ju
        if redirect_action:
            return redirect_action
            
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
