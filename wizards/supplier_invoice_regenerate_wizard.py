# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
import base64
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SupplierInvoiceRegenerateWizard(models.TransientModel):
    _name = 'supplier.invoice.regenerate.wizard'
    _description = 'Regenerate Invoice Wizard'

    processor_id = fields.Many2one(
        'supplier.invoice.processor',
        string='Processor',
        required=True,
    )
    
    invoice_id = fields.Many2one(
        'account.move',
        string='Invoice',
        required=True,
    )
    
    pdf_file = fields.Binary(
        string='Nahrať nové PDF',
        required=True,
        help='Upload the new PDF file to regenerate invoice lines',
    )
    
    pdf_filename = fields.Char(
        string='Názov súboru',
        required=True,
    )

    def action_regenerate(self):
        """Process new PDF and update invoice lines"""
        self.ensure_one()
        
        processor = self.processor_id
        invoice = self.invoice_id
        
        if not processor.supplier_id:
            raise UserError(_('Please select a supplier before regenerating the invoice.'))
        
        try:
            # Update processor attachment with new PDF
            if processor.attachment_id:
                processor.attachment_id.write({
                    'name': self.pdf_filename,
                    'datas': self.pdf_file,
                })
            else:
                attachment = self.env['ir.attachment'].create({
                    'name': self.pdf_filename,
                    'type': 'binary',
                    'datas': self.pdf_file,
                    'mimetype': 'application/pdf',
                })
                processor.attachment_id = attachment.id
            
            # Extract data from new PDF
            pdf_data = base64.b64decode(self.pdf_file)
            extracted_text = processor._extract_text_from_pdf(pdf_data, first_page_only=True)
            
            # Quick check: is this an O2 or Telekom invoice?
            is_o2_preliminary = 'o2 slovakia' in extracted_text.lower() or 'o2 sk' in extracted_text.lower()
            is_telekom_preliminary = 'slovak telekom' in extracted_text.lower()
            
            # If not O2 or Telekom, extract all pages instead
            if not is_o2_preliminary and not is_telekom_preliminary:
                extracted_text = processor._extract_text_from_pdf(pdf_data, first_page_only=False)
            
            processor.extracted_text = extracted_text
            
            # Parse invoice data
            invoice_data = processor._parse_invoice_data(extracted_text, pdf_data)
            
            # Update processor fields
            processor.is_refund = invoice_data.get('is_refund', False)
            processor.invoice_number = invoice_data.get('invoice_number')
            processor.invoice_date = invoice_data.get('invoice_date')
            processor.invoice_due_date = invoice_data.get('invoice_due_date')
            processor.total_untaxed = invoice_data.get('total_untaxed', 0.0)
            processor.total_tax = invoice_data.get('total_tax', 0.0)
            processor.total_amount = invoice_data.get('total_amount', 0.0)
            
            # Store old processor line names before deleting them
            old_line_names = [line.name for line in processor.line_ids]
            
            # Clear existing processor lines
            processor.line_ids.unlink()
            
            # Create new processor lines from extracted data
            for idx, line_data in enumerate(invoice_data.get('lines', [])):
                # Try to preserve the name from old lines if available
                old_name = None
                if idx < len(old_line_names):
                    old_name = old_line_names[idx]
                
                # Use extracted name, or fall back to old name, or use generic
                line_name = (line_data.get('name') or '').strip() or old_name or f'Invoice Line {idx + 1}'
                
                self.env['supplier.invoice.processor.line'].create({
                    'processor_id': processor.id,
                    'name': line_name,
                    'quantity': line_data.get('quantity', 1.0),
                    'price_unit': line_data.get('price_unit', 0.0),
                    'vat_rate': line_data.get('vat_rate', 0.0),
                    'currency_id': processor.currency_id.id,
                })
            
            # Delete all lines from the existing invoice
            # First, check if invoice is posted and reset to draft if needed
            if invoice.state != 'draft':
                try:
                    invoice.button_draft()
                except Exception as e:
                    _logger.warning(f"Could not reset invoice to draft: {str(e)}")
            
            # Clear all invoice lines using write() with (5, 0, 0) to remove all
            # This is safer than unlink() for posted invoices
            invoice.write({
                'invoice_line_ids': [(5, 0, 0)]
            })
            
            # Create new invoice lines from processor lines
            move_type = 'in_refund' if processor.is_refund else 'in_invoice'
            
            for line in processor.line_ids:
                # For refunds, ensure both quantity and price_unit are positive
                if move_type == 'in_refund':
                    quantity = abs(line.quantity) if line.quantity else 1.0
                    price_unit = abs(line.price_unit)
                else:
                    quantity = line.quantity
                    price_unit = line.price_unit
                
                line_vals = {
                    'name': line.name or 'Invoice Line',
                    'quantity': quantity,
                    'price_unit': price_unit,
                    'product_id': line.product_id.id if line.product_id else False,
                }
                
                # Apply VAT tax if rate is specified
                if line.vat_rate > 0:
                    tax = self.env['account.tax'].search([
                        ('type_tax_use', '=', 'purchase'),
                        ('amount', '=', line.vat_rate),
                        ('company_id', '=', processor.company_id.id),
                    ], limit=1)
                    
                    if tax:
                        line_vals['tax_ids'] = [(6, 0, [tax.id])]
                
                # Always use account 501000 (Spotreba materiálu) for all invoice lines
                account_501000 = self.env['account.account'].search([
                    ('code', '=', '501000'),
                ], limit=1)
                
                if account_501000:
                    line_vals['account_id'] = account_501000.id
                else:
                    account = self.env['account.account'].search([
                        ('account_type', '=', 'expense'),
                    ], limit=1)
                    
                    if account:
                        line_vals['account_id'] = account.id
                
                invoice.write({
                    'invoice_line_ids': [(0, 0, line_vals)]
                })
            
            # Update invoice metadata
            invoice.write({
                'invoice_date': processor.invoice_date or fields.Date.today(),
                'invoice_date_due': processor.invoice_due_date,
                'ref': processor.invoice_number or processor.name,
            })
            
            processor.state = 'done'
            processor.message_post(body=_('Invoice %s regenerated with new PDF lines.') % invoice.name)
            
            # Close wizard and show the invoice
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'res_id': invoice.id,
                'view_mode': 'form',
                'target': 'current',
            }
            
        except Exception as e:
            _logger.exception("Error regenerating invoice: %s", str(e))
            processor.state = 'error'
            processor.error_message = str(e)
            raise UserError(_('Error regenerating invoice: %s') % str(e))
