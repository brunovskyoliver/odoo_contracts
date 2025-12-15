# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ProductPairingWizard(models.TransientModel):
    """Wizard to pair invoice line with product"""
    _name = 'product.pairing.wizard'
    _description = 'Product Pairing Wizard'
    
    line_id = fields.Many2one(
        'supplier.invoice.processor.line',
        string='Invoice Line',
        required=True,
    )
    
    description = fields.Char(
        string='Popis z faktúry',
        readonly=True,
    )
    
    supplier_id = fields.Many2one(
        'res.partner',
        string='Dodávateľ',
        domain=[('supplier_rank', '>', 0)],
    )
    
    product_id = fields.Many2one(
        'product.product',
        string='Produkt',
        required=True,
        help='Vyberte produkt, ktorý zodpovedá tomuto popisu',
    )
    
    create_pairing_rule = fields.Boolean(
        string='Zapamätať párovanie',
        default=True,
        help='Vytvorí pravidlo pre budúce automatické párovanie',
    )
    
    apply_to_all = fields.Boolean(
        string='Aplikovať na všetky rovnaké riadky',
        default=True,
        help='Spáruje všetky nepárované riadky s rovnakým popisom',
    )
    
    def action_pair(self):
        """Pair the line with selected product"""
        self.ensure_one()
        
        if not self.product_id:
            raise UserError(_('Vyberte produkt!'))
        
        # Get the current line price (absolute value for credit notes)
        line_price = abs(self.line_id.price_unit)
        
        # Check if we need to update the product price
        current_product_cost = self.product_id.standard_price
        # Use max to ensure we always have the highest price
        new_cost = max(current_product_cost, line_price)
        
        if new_cost != current_product_cost:
            # Update product with new prices: cost = max(current, new), selling = cost * 1.05
            new_selling_price = new_cost * 1.05
            self.product_id.write({
                'standard_price': new_cost,
                'list_price': new_selling_price,
            })
            update_message = _(' (Cena produktu aktualizovaná na %s + 5%%)') % new_cost
        else:
            update_message = ''
        
        # Create pairing rule if requested
        if self.create_pairing_rule:
            self.env['product.pairing.rule'].create_pairing(
                self.description,
                self.product_id.id,
                self.supplier_id.id if self.supplier_id else None,
            )
        
        # Apply to current line
        self.line_id.product_id = self.product_id.id
        
        # Apply to all matching lines if requested
        if self.apply_to_all:
            matching_lines = self.env['supplier.invoice.processor.line'].search([
                ('name', '=', self.description),
                ('product_id', '=', False),
                ('id', '!=', self.line_id.id),
            ])
            matching_lines.write({'product_id': self.product_id.id})
            
            if matching_lines:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Úspech'),
                        'message': _('%d riadkov bolo spárovaných s produktom %s%s') % (
                            len(matching_lines) + 1, self.product_id.name, update_message
                        ),
                        'type': 'success',
                    },
                }
        
        return {'type': 'ir.actions.act_window_close'}
