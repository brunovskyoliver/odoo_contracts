from odoo import models, api, fields

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # Quantity alert configuration fields
    alert_qty_enabled = fields.Boolean(
        string='Upozornenie na nízku zásobu',
        default=False,
        help='Enable low stock quantity alerts for this product'
    )
    minimum_alert_qty = fields.Integer(
        string='Minimálna zásoba',
        default=2,
        help='Alert will be sent when stock is below this quantity'
    )
    alert_frequency_per_week = fields.Integer(
        string='Frekvencia upozornenia za týždeň',
        default=1,
        help='Maximum number of alerts to send per week (1=once a week, 2=twice a week, etc.)'
    )
    
    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)        
        # Always make products storable
        defaults['is_storable'] = True

        # Check if we're in a supplier invoice context
        invoice_type = self.env.context.get('default_move_type')
        create_from_invoice = self.env.context.get('create_from_supplier_invoice')
        
        if invoice_type == 'in_invoice' or create_from_invoice:
            # Set income account to "601000 Tržby za vlastné výrobky"
            defaults['property_account_income_id'] = 207
            
        return defaults