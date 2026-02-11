# Copyright 2025 NOVEM IT s.r.o.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, api


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_add_reminder_fee(self, second_reminder=False):
        reminder_product = self.env.ref('contract.product_reminder_fee_2' if second_reminder else 'contract.product_reminder_fee')
        fee_name = '2. Spoplatnená upomienka' if second_reminder else '1. Spoplatnená upomienka'
        fee_amount = 20.00 if second_reminder else 6.00
        
        for invoice in self:
            if invoice.move_type == 'out_invoice' and invoice.state == 'posted':
                # Check if reminder fee is already added
                if not any(line.product_id.id == reminder_product.id for line in invoice.invoice_line_ids):
                    # Filter taxes to only include those matching the invoice's company
                    tax_ids = reminder_product.taxes_id.filtered(
                        lambda t: t.company_id.id == invoice.company_id.id
                    ).ids
                    invoice.button_draft()
                    invoice.write({
                        'invoice_line_ids': [(0, 0, {
                            'product_id': reminder_product.id,
                            'name': fee_name,
                            'quantity': 1,
                            'price_unit': fee_amount,
                            'tax_ids': [(6, 0, tax_ids)],
                        })]
                    })
                    invoice.action_post()
        return True

    def action_add_second_reminder_fee(self):
        return self.action_add_reminder_fee(second_reminder=True)
