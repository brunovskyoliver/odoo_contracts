from odoo import Command, api, fields, models, _
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class MobileInvoiceSettings(models.TransientModel):
    _name = 'contract.mobile.invoice.settings'
    _description = 'Mobile Invoice Settings'

    invoice_date = fields.Date(string='Fakturacia mobilky date')
    context_action = fields.Char(string='Context Action')

    def action_update_contract_dates(self):
        contracts = self.env['contract.contract'].search([
            ('x_contract_type', '=', 'Mobilky'),
            # ('id', '=', 74)
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
            ('recurring_next_date', '<=', end_of_month),
            # ('state', 'in', ['in-progress']),
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
        _logger.info("Starting monthly removal of excess usage contract lines")
        
        # Find the products for excess usage
        product_0 = self.env['product.product'].search([
            ('display_name', '=', 'Vyúčtovanie paušálnych služieb a spotreby HLAS 0%')
        ], limit=1)
        product_23 = self.env['product.product'].search([
            ('display_name', '=', 'Vyúčtovanie paušálnych služieb a spotreby HLAS 23%')
        ], limit=1)

        if not product_0 and not product_23:
            _logger.error("Could not find Vyúčtovanie products")
            return

        # Find all contract lines with these products
        domain = []
        if product_0:
            domain.append(('product_id', '=', product_0.id))
        if product_23:
            if domain:
                domain = ['|'] + domain + [('product_id', '=', product_23.id)]
            else:
                domain = [('product_id', '=', product_23.id)]

        contract_lines = self.env['contract.line'].search(domain)
        
        if not contract_lines:
            _logger.info("No excess usage contract lines found to remove")
            return

        removed_count = len(contract_lines)
        try:
            # Remove all matching contract lines
            contract_lines.unlink()
            _logger.info(f"Successfully removed {removed_count} excess usage contract lines")
        except Exception as e:
            _logger.error(f"Error removing excess usage contract lines: {str(e)}")
            raise

    def _get_hlas_invoice_products(self):
        product_23 = self.env['product.product'].search([
            ('display_name', '=', 'Vyúčtovanie paušálnych služieb a spotreby HLAS 23%')
        ], limit=1)
        product_0 = self.env['product.product'].search([
            ('display_name', '=', 'Vyúčtovanie paušálnych služieb a spotreby HLAS 0%')
        ], limit=1)
        return product_23, product_0

    def action_create_hlas_only_invoices(self):
        self.ensure_one()
        if not self.invoice_date:
            raise UserError(_('Prosim nastavte datum fakturacie.'))

        product_23, product_0 = self._get_hlas_invoice_products()
        if not product_23 and not product_0:
            raise UserError(_(
                'Produkty "Vyúčtovanie paušálnych služieb a spotreby HLAS 23%%" '
                'a "Vyúčtovanie paušálnych služieb a spotreby HLAS 0%%" neboli najdene.'
            ))

        allowed_product_ids = (product_23 | product_0).ids
        date_ref = self.invoice_date

        contracts = self.env['contract.contract'].search([
            ('x_contract_type', '=', 'Mobilky'),
            ('recurring_next_date', '<=', date_ref),
        ])

        created_moves = self.env['account.move']
        invoiced_lines = self.env['contract.line']

        for contract in contracts:
            lines_to_invoice = contract._get_lines_to_invoice(date_ref).filtered(
                lambda l: not l.display_type and l.product_id.id in allowed_product_ids
            )
            if not lines_to_invoice:
                continue

            invoice_vals = contract._prepare_invoice(date_ref)
            invoice_vals['invoice_line_ids'] = []

            for line in lines_to_invoice:
                invoice_line_vals = line._prepare_invoice_line()
                if not invoice_line_vals:
                    continue
                invoice_line_vals.pop('company_id', None)
                invoice_line_vals.pop('company_currency_id', None)
                invoice_vals['invoice_line_ids'].append(Command.create(invoice_line_vals))

            if not invoice_vals['invoice_line_ids']:
                continue

            move = self.env['account.move'].create(invoice_vals)
            contract._copy_mobile_usage_reports_to_invoice(move)
            created_moves |= move
            invoiced_lines |= lines_to_invoice

        if not created_moves:
            raise UserError(_('Nenasli sa ziadne mobilne polozky na fakturaciu pre zvoleny datum.'))

        invoiced_lines._update_recurring_next_date()
        created_moves.action_post()
        contracts._compute_recurring_next_date()

        action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_out_invoice_type')
        action['domain'] = [('id', 'in', created_moves.ids)]
        if len(created_moves) == 1:
            action['views'] = [(False, 'form')]
            action['res_id'] = created_moves.id
        return action