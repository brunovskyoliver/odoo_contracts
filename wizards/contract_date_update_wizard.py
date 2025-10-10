from odoo import api, fields, models
import logging

_logger = logging.getLogger(__name__)

class ContractDateUpdateWizard(models.TransientModel):
    _name = 'contract.date.update.wizard'
    _description = 'Contract Date Update Wizard'

    update_date = fields.Date(string='Date to Update', required=True, default=fields.Date.today)

    def action_update_dates(self):
        """Update contract and contract line dates based on the selected date"""
        self.ensure_one()
        active_ids = self.env.context.get('active_ids', [])
        contracts = self.env['contract.contract'].browse(active_ids)

        for contract in contracts:
            _logger.info(f"Processing contract {contract.name} (ID: {contract.id})")
            
            # Get today's date for the domain
            today = fields.Date.context_today(self)
            
            # Get today's date for the domain
            today = fields.Date.context_today(self)
            
            if contract.x_contract_type == 'Mobilky':
                _logger.info(f"Processing Mobilky contract: {contract.name}")
                # For Mobilky contracts, use the same domain as the _get_state_domain method for "in-progress" state
                contract_lines = self.env['contract.line'].search([
                    ('contract_id', '=', contract.id),
                    ('date_start', '<=', today),
                    ('is_canceled', '=', False),
                    '|',
                    ('date_end', '>=', today),
                    ('date_end', '=', False),
                    '|',
                    ('is_auto_renew', '=', True),
                    '&',
                    ('is_auto_renew', '=', False),
                    ('termination_notice_date', '>', today),
                ])
                
                _logger.info(f"Found {len(contract_lines)} contract lines in 'in-progress' state for Mobilky contract {contract.id}")
            else:
                _logger.info(f"Processing regular contract: {contract.name}")
                # For non-Mobilky contracts, get lines with recurring_interval = 1 and not cancelled
                contract_lines = self.env['contract.line'].search([
                    ('contract_id', '=', contract.id),
                    ('recurring_interval', '=', 1),
                    ('date_start', '<=', today),
                    ('is_canceled', '=', False),
                    '|',
                    ('date_end', '>=', today),
                    ('date_end', '=', False),
                    '|',
                    ('is_auto_renew', '=', True),
                    '&',
                    ('is_auto_renew', '=', False),
                    ('termination_notice_date', '>', today),
                ])
                
                _logger.info(f"Found {len(contract_lines)} contract lines with recurring_interval=1 for regular contract {contract.id}")
            
            if not contract_lines:
                # Fall back to lines that are not canceled
                contract_lines = self.env['contract.line'].search([
                    ('contract_id', '=', contract.id),
                    ('is_canceled', '=', False),
                ])
                _logger.info(f"Found {len(contract_lines)} non-canceled lines as fallback")
                
            if not contract_lines:
                _logger.info(f"No eligible contract lines found for contract {contract.id}")
                continue
                
            _logger.info(f"Found {len(contract_lines)} eligible contract lines with IDs: {contract_lines.ids}")
            
            # Check if we have valid IDs
            if not contract_lines.ids:
                _logger.info(f"No valid IDs found in contract lines for contract {contract.id}")
                continue
            
            try:
                # Convert IDs to proper format for SQL IN clause
                ids_tuple = tuple(contract_lines.ids) if len(contract_lines.ids) > 1 else (contract_lines.ids[0],)
                
                # Update date_start for all lines
                _logger.info(f"Updating date_start for {len(contract_lines)} lines with date {self.update_date}")
                self.env.cr.execute("""
                    UPDATE contract_line 
                    SET date_start = %s 
                    WHERE id IN %s
                """, (self.update_date, ids_tuple))
                
                # Update recurring_next_date for all lines
                _logger.info(f"Updating recurring_next_date for {len(contract_lines)} lines with date {self.update_date}")
                self.env.cr.execute("""
                    UPDATE contract_line 
                    SET recurring_next_date = %s
                    WHERE id IN %s
                """, (self.update_date, ids_tuple))
                
                # Update the contract's recurring_next_date and date_start
                _logger.info(f"Updating recurring_next_date and date_start for contract {contract.id}")
                self.env.cr.execute("""
                    UPDATE contract_contract 
                    SET recurring_next_date = %s, date_start = %s 
                    WHERE id = %s
                """, (self.update_date, self.update_date, contract.id))
                
                # Invalidate the cache for all affected records
                contract_lines.invalidate_recordset()
                contract.invalidate_recordset()
                
                _logger.info(f"Successfully updated dates for contract {contract.id}")
                
            except Exception as e:
                _logger.error(f"Error updating dates for contract {contract.id}: {str(e)}")
                _logger.error(f"Contract line IDs: {contract_lines.ids}")
                self.env.cr.rollback()
                # Continue with other contracts instead of raising the error
                continue
                
        return {'type': 'ir.actions.client', 'tag': 'reload'}