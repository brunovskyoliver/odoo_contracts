from odoo import models, api, fields

class ProductTemplate(models.Model):
    _inherit = 'product.template'
    
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
