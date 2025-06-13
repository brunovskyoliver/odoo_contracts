# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import pandas as pd
import io
import re
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class ContractMobileInvoiceImport(models.TransientModel):
    _name = "contract.mobile.invoice.import"
    _description = "Mobile Invoice Import Wizard"

    name = fields.Char(string="Reference", required=True)
    date = fields.Date(
        string="Invoice Date", 
        required=True,
        default=fields.Date.context_today
    )
    operator = fields.Selection(
        selection=[
            ('telekom', 'Telekom'),
            ('o2', 'O2'),
        ],
        string="Operator",
        required=True,
    )
    csv_file = fields.Binary(string="CSV File", required=True)
    csv_filename = fields.Char(string="CSV File Name")
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    notes = fields.Text(string="Notes")

    def action_import(self):
        """Import the CSV file and create a contract.mobile.invoice record"""
        self.ensure_one()
        
        # Create the mobile invoice record
        invoice = self.env['contract.mobile.invoice'].create({
            'name': self.name,
            'date': self.date,
            'operator': self.operator,
            'csv_file': self.csv_file,
            'csv_filename': self.csv_filename,
            'notes': self.notes,
            'company_id': self.company_id.id,
            'state': 'draft',
        })
        
        # Process the CSV file
        invoice.action_process_invoice()
        
        # Return an action to open the created invoice
        return {
            'name': _('Mobile Invoice'),
            'view_mode': 'form',
            'res_model': 'contract.mobile.invoice',
            'res_id': invoice.id,
            'type': 'ir.actions.act_window',
        }
    
    @api.model
    def _process_csv_file(self, csv_content, operator):
        """Process CSV file based on operator"""
        try:
            if operator == 'telekom':
                return self._process_telekom_csv(csv_content)
            elif operator == 'o2':
                return self._process_o2_csv(csv_content)
            else:
                raise UserError(_("Unsupported operator: %s") % operator)
        except Exception as e:
            raise UserError(_("Error processing CSV file: %s") % str(e))

    @api.model
    def _process_telekom_csv(self, csv_content):
        """Process Telekom CSV file"""
        df = pd.read_csv(io.StringIO(csv_content.decode('utf-8')), low_memory=False)
        result = []
        
        # Process rows based on the Telekom CSV structure
        for _, row in df.iterrows():
            try:
                # Here we'll implement the logic from nadspotreba.py for Telekom
                phone_col = 'Charges__BudgetCentre__MSISDN__@Value'
                desc_col = 'Charges__BudgetCentre__ProductFamily__Charge__@Desc'
                amount_col = 'Charges__BudgetCentre__ProductFamily__Charge__@Amount'
                
                if phone_col in row and pd.notna(row[phone_col]):
                    phone_number = str(row[phone_col])
                    service_name = row[desc_col] if pd.notna(row[desc_col]) else ''
                    
                    # Use the safe conversion helper for amount
                    amount = self._safe_convert_to_float(row[amount_col])
                    if amount == 0.0 and pd.notna(row[amount_col]):
                        self._logger.warning(f"Amount for {phone_number}, service '{service_name}' converted to 0.0 from original value: '{row[amount_col]}'")
                        # Try debugging the value
                        self._logger.info(f"Amount type: {type(row[amount_col])}, value: {row[amount_col]!r}")
                    
                    # Determine service type based on description
                    service_type = 'other'
                    if 'T-Biznis' in service_name:
                        service_type = 'basic'
                    elif 'Data' in service_name or 'dáta' in service_name.lower():
                        service_type = 'data'
                    elif 'Hovory' in service_name or 'hovor' in service_name.lower():
                        service_type = 'voice'
                    elif 'SMS' in service_name:
                        service_type = 'sms'
                    elif 'MMS' in service_name:
                        service_type = 'mms'
                    elif 'Roaming' in service_name:
                        service_type = 'roaming'
                    
                    # Check if it's excess usage
                    is_excess = False
                    if service_type in ['data', 'voice', 'sms', 'mms', 'roaming']:
                        is_excess = True
                    
                    result.append({
                        'phone_number': phone_number,
                        'service_name': service_name,
                        'service_type': service_type,
                        'amount': amount,
                        'total': amount,
                        'is_excess_usage': is_excess,
                    })
            
            except Exception as e:
                # Log the error but continue processing other rows
                _logger.error(f"Error processing row: {str(e)}")
        
        return result

    @api.model
    def _process_o2_csv(self, csv_content):
        """Process O2 CSV file"""
        # First, preprocess to fill in missing phone numbers
        csv_data = csv_content.decode('utf-8')
        lines = csv_data.strip().split('\n')
        
        # Find the phone number column index
        header = lines[0].split(',')
        msisdn_col_idx = -1
        for i, col in enumerate(header):
            if 'MSISDN' in col:
                msisdn_col_idx = i
                break
        
        if msisdn_col_idx == -1:
            raise UserError(_("Could not find MSISDN column in O2 CSV file"))
        
        # Forward fill the phone numbers
        processed_lines = [lines[0]]
        current_number = None
        
        for line in lines[1:]:
            cols = line.split(',')
            if len(cols) <= msisdn_col_idx:
                # Skip malformed lines
                continue
                
            if cols[msisdn_col_idx].strip():
                current_number = cols[msisdn_col_idx]
            elif current_number:
                cols[msisdn_col_idx] = current_number
                
            processed_lines.append(','.join(cols))
        
        processed_csv = '\n'.join(processed_lines)
        
        # Now process the preprocessed CSV
        df = pd.read_csv(io.StringIO(processed_csv), low_memory=False)
        result = []
        
        # Known column names for O2 CSV
        phone_col = [col for col in df.columns if 'MSISDN' in col][0]
        fee_type_col = 'Subscribers__Subscriber__InvoiceLines__FeeLines__Fee__FeeType'
        fee_name_col = 'Subscribers__Subscriber__InvoiceLines__FeeLines__Fee__FeeName'
        amount_col = 'Subscribers__Subscriber__InvoiceLines__FeeLines__Fee__PayableAmount'
        
        # Process rows based on the O2 CSV structure
        for _, row in df.iterrows():
            try:
                if phone_col in row and pd.notna(row[phone_col]):
                    phone_number = str(row[phone_col])
                    fee_type = row[fee_type_col] if pd.notna(row[fee_type_col]) else ''
                    service_name = row[fee_name_col] if pd.notna(row[fee_name_col]) else ''
                    # Use the safe conversion helper for amount
                    amount = self._safe_convert_to_float(row[amount_col])
                    if amount == 0.0 and pd.notna(row[amount_col]):
                        self._logger.warning(f"Amount for {phone_number}, service '{service_name}' converted to 0.0 from original value: '{row[amount_col]}'")
                        # Try debugging the value
                        self._logger.info(f"Amount type: {type(row[amount_col])}, value: {row[amount_col]!r}")
                    
                    # Determine service type based on fee type and name
                    service_type = 'other'
                    if fee_type == 'recurring_arrears' and not service_name.lower().startswith('vpn'):
                        service_type = 'basic'
                    elif 'data' in service_name.lower():
                        service_type = 'data'
                    elif 'voice' in service_name.lower() or 'call' in service_name.lower():
                        service_type = 'voice'
                    elif 'sms' in service_name.lower():
                        service_type = 'sms'
                    elif 'mms' in service_name.lower():
                        service_type = 'mms'
                    elif 'roaming' in service_name.lower():
                        service_type = 'roaming'
                    
                    # Check if it's excess usage
                    is_excess = False
                    if service_type in ['data', 'voice', 'sms', 'mms', 'roaming']:
                        is_excess = True
                    
                    result.append({
                        'phone_number': phone_number,
                        'service_name': service_name,
                        'service_type': service_type,
                        'amount': amount,
                        'total': amount,
                        'is_excess_usage': is_excess,
                    })
            
            except Exception as e:
                # Log the error but continue processing other rows
                _logger.error(f"Error processing row: {str(e)}")
        
        return result

    def _safe_convert_to_float(self, value):
        """Safely convert a string value to float, handling different formats."""
        if not value or pd.isna(value):
            return 0.0
            
        if isinstance(value, (int, float)):
            return float(value)
            
        # Clean the string
        value_str = str(value).strip()
        
        # Try direct conversion first
        try:
            return float(value_str)
        except ValueError:
            # Try replacing comma with period
            try:
                return float(value_str.replace(',', '.'))
            except ValueError:
                # Try replacing spaces and other characters
                try:
                    cleaned_value = re.sub(r'[^\d.,]', '', value_str).replace(',', '.')
                    return float(cleaned_value)
                except ValueError:
                    return 0.0


class ContractMobileGenerateReport(models.TransientModel):
    _name = "contract.mobile.generate.report"
    _description = "Generate Mobile Usage Report"

    invoice_id = fields.Many2one(
        comodel_name="contract.mobile.invoice",
        string="Invoice",
        required=True,
        domain=[('state', '=', 'processed')],
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        required=True,
    )
    date = fields.Date(
        string="Report Date", 
        required=True,
        default=fields.Date.context_today
    )
    name = fields.Char(
        string="Report Name", 
        required=True,
        default=lambda self: _("Usage Report"),
    )
    
    def action_generate(self):
        """Generate the mobile usage report"""
        self.ensure_one()
        
        # Create the report record
        report = self.env['contract.mobile.usage.report'].create({
            'name': self.name,
            'date': self.date,
            'partner_id': self.partner_id.id,
            'invoice_id': self.invoice_id.id,
            'state': 'draft',
        })
        
        # Generate the report
        report.action_generate_report()
        
        # Return an action to open the created report
        return {
            'name': _('Mobile Usage Report'),
            'view_mode': 'form',
            'res_model': 'contract.mobile.usage.report',
            'res_id': report.id,
            'type': 'ir.actions.act_window',
        }
