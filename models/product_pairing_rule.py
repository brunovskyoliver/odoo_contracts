# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class ProductPairingRule(models.Model):
    """
    Stores learned mappings between invoice line descriptions and products.
    Used to automatically match future invoice lines to products.
    """
    _name = 'product.pairing.rule'
    _description = 'Product Pairing Rule'
    _order = 'create_date desc'
    
    name = fields.Char(
        string='Popis z faktúry',
        required=True,
        index=True,
        help='Popis produktu z dodávateľskej faktúry',
    )
    
    product_id = fields.Many2one(
        'product.product',
        string='Spárovaný produkt',
        required=True,
        ondelete='cascade',
        help='Produkt v Odoo, ktorý zodpovedá tomuto popisu',
    )
    
    supplier_id = fields.Many2one(
        'res.partner',
        string='Dodávateľ',
        domain=[('supplier_rank', '>', 0)],
        help='Voliteľné: Párovanie platí len pre konkrétneho dodávateľa',
    )
    
    active = fields.Boolean(
        string='Aktívne',
        default=True,
        help='Neaktívne pravidlá sa nepoužívajú pri automatickom párovaní',
    )
    
    usage_count = fields.Integer(
        string='Počet použití',
        default=0,
        readonly=True,
        help='Koľkokrát bolo toto pravidlo použité',
    )
    
    last_used_date = fields.Datetime(
        string='Naposledy použité',
        readonly=True,
    )
    
    pack_qty = fields.Integer(
        string='Množstvo v balíčku',
        default=1,
        help='Počet jednotiek produktu v jednom balíčku (napr. 5 pre 5-pack). Používa sa na vynásobenie množstva z faktúry.',
    )
    
    _sql_constraints = [
        ('unique_pairing', 
         'unique(name, supplier_id)', 
         'Párovanie pre tento popis a dodávateľa už existuje!'),
    ]
    
    @api.model
    def find_product_for_description(self, description, supplier_id=None):
        """
        Find matching product for given description.
        Returns product.product record or False.
        """
        if not description:
            return False
        
        # First try exact match with supplier
        if supplier_id:
            rule = self.search([
                ('name', '=ilike', description),
                ('supplier_id', '=', supplier_id),
                ('active', '=', True),
            ], limit=1)
            if rule:
                rule.write({
                    'usage_count': rule.usage_count + 1,
                    'last_used_date': fields.Datetime.now(),
                })
                return rule.product_id
        
        # Then try exact match without supplier
        rule = self.search([
            ('name', '=ilike', description),
            ('supplier_id', '=', False),
            ('active', '=', True),
        ], limit=1)
        
        if rule:
            rule.write({
                'usage_count': rule.usage_count + 1,
                'last_used_date': fields.Datetime.now(),
            })
            return rule.product_id
        
        # Try fuzzy match (contains)
        if supplier_id:
            rule = self.search([
                ('name', 'ilike', description),
                ('supplier_id', '=', supplier_id),
                ('active', '=', True),
            ], limit=1)
            if rule:
                rule.write({
                    'usage_count': rule.usage_count + 1,
                    'last_used_date': fields.Datetime.now(),
                })
                return rule.product_id
        
        return False
    
    @api.model
    def create_pairing(self, description, product_id, supplier_id=None, pack_qty=1):
        """Create a new pairing rule."""
        if not description or not product_id:
            raise ValidationError(_('Popis a produkt sú povinné!'))
        
        # Check if pairing already exists
        existing = self.search([
            ('name', '=ilike', description),
            ('supplier_id', '=', supplier_id),
        ])
        
        if existing:
            # Update existing
            existing.write({
                'product_id': product_id,
                'active': True,
                'pack_qty': pack_qty,
            })
            return existing
        
        # Create new
        return self.create({
            'name': description,
            'product_id': product_id,
            'supplier_id': supplier_id,
            'pack_qty': pack_qty,
        })
