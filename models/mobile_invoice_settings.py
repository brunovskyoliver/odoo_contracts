from odoo import api, fields, models
from dateutil.relativedelta import relativedelta
import logging

_logger = logging.getLogger(__name__)

class MobileInvoiceSettings(models.TransientModel):
    _name = 'contract.mobile.invoice.settings'
    _description = 'Mobile Invoice Settings'

    invoice_date = fields.Date(string='Fakturacia mobilky date')
    context_action = fields.Char(string='Context Action')

    def action_update_contract_dates(self):
        contracts = self.env['contract.contract'].search([
            ('x_contract_type', '=', 'Mobilky')
        ])
        
        _logger.info(f"Found {len(contracts)} mobile contracts to process")
        
        # Get the start and end of the month for the invoice date
        start_of_month = self.invoice_date.replace(day=1)
        end_of_month = (start_of_month + relativedelta(months=1, days=-1))
        
        # Get all contract lines in one query
        all_contract_lines = self.env['contract.line'].search([
            ('contract_id', 'in', contracts.ids),
            ('recurring_next_date', '!=', False),
            ('recurring_next_date', '>=', start_of_month),
            ('recurring_next_date', '<=', end_of_month)
        ])
        
        if not all_contract_lines:
            _logger.info("No eligible contract lines found")
            return {'type': 'ir.actions.client', 'tag': 'reload'}
            
        _logger.info(f"Found {len(all_contract_lines)} eligible contract lines")
        
        # Get lines that need date_start update
        lines_to_update_date_start = all_contract_lines.filtered(
            lambda l: l.date_start and l.date_start > self.invoice_date
        )
        
        try:
            if lines_to_update_date_start:
                _logger.info(f"Updating date_start for {len(lines_to_update_date_start)} lines")
                
                # Update all date_start values at once
                self.env.cr.execute("""
                    UPDATE contract_line 
                    SET date_start = %s 
                    WHERE id IN %s
                """, (self.invoice_date, tuple(lines_to_update_date_start.ids)))
                self.env.cr.commit()
            
            # Update all recurring_next_date values at once for contract lines
            _logger.info(f"Updating recurring_next_date for {len(all_contract_lines)} lines")
            self.env.cr.execute("""
                UPDATE contract_line 
                SET recurring_next_date = %s
                WHERE id IN %s
            """, (self.invoice_date, tuple(all_contract_lines.ids)))
            self.env.cr.commit()
            
            # Get unique contract IDs that had lines updated
            affected_contract_ids = all_contract_lines.mapped('contract_id').ids
            
            # Update the contracts' recurring_next_date
            if affected_contract_ids:
                _logger.info(f"Updating recurring_next_date for {len(affected_contract_ids)} contracts")
                self.env.cr.execute("""
                    UPDATE contract_contract 
                    SET recurring_next_date = %s, date_start = %s 
                    WHERE id IN %s
                """, (self.invoice_date, self.invoice_date, tuple(affected_contract_ids)))
                self.env.cr.commit()
            
            # Invalidate the cache for all affected records
            all_contract_lines.invalidate_recordset()
            all_contract_lines.mapped('contract_id').invalidate_recordset()
            
            _logger.info("\nSummary of changes made:")
            _logger.info(f"Total contracts processed: {len(affected_contract_ids)}")
            _logger.info(f"Total lines updated: {len(all_contract_lines)}")
            _logger.info(f"Total date_start fields changed: {len(lines_to_update_date_start)}")
            
        except Exception as e:
            _logger.error(f"Error during update: {str(e)}")
            self.env.cr.rollback()
            raise
            
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_reset_excess_usage_lines(self):
        """Reset all excess usage contract lines"""
        _logger.info("Starting monthly reset of excess usage contract lines")
        
        # Find the product for excess usage
        product = self.env['product.product'].search([
            ('display_name', '=', 'Vyúčtovanie paušálnych služieb a spotreby HLAS')
        ], limit=1)
        
        if not product:
            _logger.error("Could not find product 'Vyúčtovanie paušálnych služieb a spotreby HLAS'")
            return
            
        # Find all contract lines with this product
        contract_lines = self.env['contract.line'].search([
            ('product_id', '=', product.id)
        ])
        
        reset_count = 0
        for line in contract_lines:
            try:
                # Reset the line amount to 0
                line.write({
                    'price_unit': 0.0,
                    'x_zlavnena_cena': 0.0
                })
                reset_count += 1
            except Exception as e:
                _logger.error(f"Error resetting contract line {line.id}: {str(e)}")
                
        _logger.info(f"Successfully reset {reset_count} excess usage contract lines")