from odoo import api, fields, models

class MobileInvoiceSettings(models.TransientModel):
    _name = 'contract.mobile.invoice.settings'
    _description = 'Mobile Invoice Settings'

    invoice_date = fields.Date(string='Fakturacia mobilky date', required=True)

    def action_update_contract_dates(self):
        contracts = self.env['contract.contract'].search([
            ('x_contract_type', '=', 'Mobilky')
        ])
        
        for contract in contracts:
            # Get all lines that match the contract's recurring_next_date
            eligible_lines = contract.contract_line_ids.filtered(
                lambda l: l.recurring_next_date == contract.recurring_next_date
            )
            
            if not eligible_lines:
                continue
            
            # First, update all date_start values where needed
            for line in eligible_lines:
                if line.date_start and line.date_start > self.invoice_date:
                    line.with_context(skip_date_check=True).write({
                        'date_start': self.invoice_date
                    })
            
            # Ensure date_start changes are committed
            self.env.cr.commit()
            
            # Then update recurring_next_date for all eligible lines
            eligible_lines.with_context(skip_date_check=True).write({
                'recurring_next_date': self.invoice_date
            })
            
            # Finally update the contract's recurring_next_date
            contract.write({
                'recurring_next_date': self.invoice_date
            })
            
        return {'type': 'ir.actions.client', 'tag': 'reload'}
