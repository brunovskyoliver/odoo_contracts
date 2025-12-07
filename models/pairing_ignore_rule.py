# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class PairingIgnoreRule(models.Model):
    """
    Stores descriptions that should be ignored (not paired with products).
    These lines won't appear in the unpaired lines view.
    Useful for fees like recycling, shipping, discounts, etc.
    """
    _name = 'pairing.ignore.rule'
    _description = 'Pairing Ignore Rule'
    _order = 'create_date desc'
    
    name = fields.Char(
        string='Popis na ignorovanie',
        required=True,
        index=True,
        help='Popis z faktúry, ktorý sa má ignorovať pri párovaní (napr. Recyklačný poplatok)',
    )
    
    match_type = fields.Selection(
        [
            ('exact', 'Presne (case-insensitive)'),
            ('contains', 'Obsahuje'),
            ('startswith', 'Začína s'),
        ],
        string='Typ zhody',
        default='contains',
        help='Ako sa má popis porovnávať',
    )
    
    supplier_id = fields.Many2one(
        'res.partner',
        string='Dodávateľ',
        domain=[('supplier_rank', '>', 0)],
        help='Voliteľné: Ignorovanie platí len pre konkrétneho dodávateľa',
    )
    
    active = fields.Boolean(
        string='Aktívne',
        default=True,
        help='Neaktívne pravidlá sa nepoužívajú',
    )
    
    _sql_constraints = [
        ('unique_ignore_rule', 
         'unique(name, supplier_id, match_type)', 
         'Pravidlo ignorovania pre tento popis a dodávateľa už existuje!'),
    ]
    
    @api.model
    def should_ignore_line(self, description, supplier_id=None):
        """
        Check if a line description should be ignored (not paired).
        Returns True if line should be ignored, False otherwise.
        """
        if not description:
            return False
        
        # Check rules with supplier first
        if supplier_id:
            rules = self.search([
                ('active', '=', True),
                ('supplier_id', '=', supplier_id),
            ])
            
            if self._matches_any_rule(description, rules):
                _logger.info(f"Ignoring line '{description}' (supplier-specific rule)")
                return True
        
        # Check global rules (no supplier specified)
        rules = self.search([
            ('active', '=', True),
            ('supplier_id', '=', False),
        ])
        
        result = self._matches_any_rule(description, rules)
        if result:
            _logger.info(f"Ignoring line '{description}' (global rule)")
        return result
    
    def _matches_any_rule(self, description, rules):
        """Check if description matches any of the rules."""
        desc_lower = description.lower()
        
        for rule in rules:
            rule_text = rule.name.lower()
            
            if rule.match_type == 'exact':
                if desc_lower == rule_text:
                    return True
            elif rule.match_type == 'contains':
                if rule_text in desc_lower:
                    return True
            elif rule.match_type == 'startswith':
                if desc_lower.startswith(rule_text):
                    return True
        
        return False
    
    @api.model
    def create_ignore_rule(self, description, supplier_id=None, match_type='contains'):
        """Create a new ignore rule."""
        if not description:
            raise ValidationError(_('Popis je povinný!'))
        
        # Check if rule already exists
        existing = self.search([
            ('name', '=ilike', description),
            ('supplier_id', '=', supplier_id),
            ('match_type', '=', match_type),
        ])
        
        if existing:
            # Reactivate if it was deactivated
            existing.write({'active': True})
            return existing
        
        # Create new
        return self.create({
            'name': description,
            'supplier_id': supplier_id,
            'match_type': match_type,
        })
    
    def write(self, vals):
        """Override write to recompute needs_pairing for affected lines."""
        result = super().write(vals)
        self._recompute_affected_lines()
        return result
    
    @api.model
    def create(self, vals_list):
        """Override create to recompute needs_pairing for affected lines."""
        if not isinstance(vals_list, list):
            vals_list = [vals_list]
        result = super().create(vals_list)
        result._recompute_affected_lines()
        return result
    
    def _recompute_affected_lines(self):
        """Recompute needs_pairing for all lines that might be affected by these rules."""
        # Get all processor lines
        line_model = self.env['supplier.invoice.processor.line']
        
        # For efficiency, just recompute all lines (small performance cost for correctness)
        all_lines = line_model.search([])
        if all_lines:
            all_lines._compute_needs_pairing()
            _logger.info(f"Recomputed needs_pairing for {len(all_lines)} lines after ignore rule change")
