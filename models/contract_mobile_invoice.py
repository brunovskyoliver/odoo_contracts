# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import base64
import pandas as pd
import io
import re
import logging

_logger = logging.getLogger(__name__)


class ContractMobileInvoice(models.Model):
    _name = "contract.mobile.invoice"
    _description = "Mobile Service Invoice"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = "date desc"

    name = fields.Char(string="Reference", required=True, tracking=True)
    date = fields.Date(string="Invoice Date", required=True, tracking=True)
    operator = fields.Selection(
        selection=[
            ('telekom', 'Telekom'),
            ('o2', 'O2'),
        ],
        string="Operator",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('processed', 'Processed'),
            ('done', 'Done'),
        ],
        string="State",
        default='draft',
        tracking=True,
    )
    invoice_line_ids = fields.One2many(
        comodel_name="contract.mobile.invoice.line",
        inverse_name="invoice_id",
        string="Invoice Lines",
    )
    line_count = fields.Integer(
        string="Number of Lines",
        compute="_compute_line_count",
        store=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    csv_file = fields.Binary(string="CSV File", attachment=True)
    csv_filename = fields.Char(string="CSV File Name")
    notes = fields.Text(string="Notes")
    
    @api.depends('invoice_line_ids')
    def _compute_line_count(self):
        for record in self:
            record.line_count = len(record.invoice_line_ids)
    
    def action_process_invoice(self):
        """Process the CSV file and create invoice lines"""
        self.ensure_one()
        
        if self.csv_file and self.operator:
            # Delete existing invoice lines
            self.invoice_line_ids.unlink()
            
            try:
                # Decode the CSV file
                csv_content = base64.b64decode(self.csv_file)
                
                if self.operator == 'telekom':
                    lines = self._process_telekom_csv(csv_content)
                elif self.operator == 'o2':
                    lines = self._process_o2_csv(csv_content)
                else:
                    raise UserError(_("Unsupported operator: %s") % self.operator)
                    
                # Create invoice lines
                invoice_lines = []
                for line in lines:
                    invoice_lines.append((0, 0, line))
                    
                self.write({
                    'invoice_line_ids': invoice_lines,
                    'state': 'processed',
                })
                
                return True
                
            except Exception as e:
                raise UserError(_("Error processing CSV file: %s") % str(e))
        
        return False
        
    def _process_telekom_csv(self, csv_content):
        """Process Telekom CSV file"""
        result = []
        
        try:
            # Read the CSV file into a pandas DataFrame
            df_raw = pd.read_csv(io.BytesIO(csv_content), encoding='utf-8', low_memory=False)
            
            # Group by phone number (Label column)
            grouped = df_raw.groupby("Charges__BudgetCentre__ProductFamily__Charge__@Label")
            
            for phone_number, data in grouped:
                if pd.isna(phone_number):
                    continue
                # Clean phone number to keep only numeric characters
                phone_number = self._clean_phone_number(phone_number)
                # Skip non-Slovak numbers
                if not phone_number.startswith('421'):
                    continue
                
                # Process T-Biznis services (basic plans)
                tbiznis_services = data[data['Charges__BudgetCentre__ProductFamily__Charge__@Desc'].str.contains('T-Biznis', na=False)]
                
                for _, row in tbiznis_services.iterrows():
                    service_name = row['Charges__BudgetCentre__ProductFamily__Charge__@Desc']
                    price = self._safe_convert_to_float(row['Charges__BudgetCentre__ProductFamily__Charge__@Price'])
                    vat_rate = row['Charges__BudgetCentre__ProductFamily__Charge__@VatRate']
                    
                    if pd.notna(service_name):
                        # Map T-Biznis service names to NOVEM names
                        service_name = service_name.replace("T-Biznis Flex Variant 1", "NOVEM nekonečno 6GB")
                        service_name = service_name.replace("T-Biznis Flex Variant 10", "NOVEM nekonečno 10GB")
                        service_name = service_name.replace("T-Biznis Flex Variant 11", "NOVEM nekonečno 30GB")
                        service_name = service_name.replace("T-Biznis Flex Variant 2", "NOVEM Fér bez dát")
                        service_name = service_name.replace("T-Biznis Flex Variant 3", "NOVEM nekonečno 20GB")
                        service_name = service_name.replace("T-Biznis Flex Variant 4", "NOVEM nekonečno 50GB")
                        service_name = service_name.replace("T-Biznis Flex Variant 5", "NOVEM 250 0,5GB")
                        service_name = service_name.replace("T-Biznis Flex Variant 6", "NOVEM nekonečno 0,5GB")
                        service_name = service_name.replace("T-Biznis Flex Variant 7", "NOVEM 250 30 GB")
                        service_name = service_name.replace("T-Biznis Flex Variant 8", "NOVEM 250 10 GB")
                        service_name = service_name.replace("T-Biznis Flex Variant 9", "NOVEM nekonečno bez dát")
                        
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': 'basic',
                            'amount': price,
                            'total': price,
                            'is_excess_usage': False,
                        })
                
                # Process paid services (excluding T-Biznis)
                paid_services = data[
                    (data['Charges__BudgetCentre__ProductFamily__Charge__@Price'] > 0) &
                    (~data['Charges__BudgetCentre__ProductFamily__Charge__@Desc'].str.contains('T-Biznis', na=False))
                ]
                
                for _, row in paid_services.iterrows():
                    service_name = row['Charges__BudgetCentre__ProductFamily__Charge__@Desc']
                    price = self._safe_convert_to_float(row['Charges__BudgetCentre__ProductFamily__Charge__@Price'])
                    vat_rate = row['Charges__BudgetCentre__ProductFamily__Charge__@VatRate']
                    
                    if pd.notna(service_name):
                        service_type = 'other'
                        if 'data' in service_name.lower():
                            service_type = 'data'
                        elif 'voice' in service_name.lower() or 'call' in service_name.lower():
                            service_type = 'voice'
                        elif 'sms' in service_name.lower():
                            service_type = 'sms'
                        elif 'mms' in service_name.lower():
                            service_type = 'mms'
                        elif 'roaming' in service_name.lower():
                            service_type = 'roaming'
                        
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': service_type,
                            'amount': price,
                            'total': price,
                            'is_excess_usage': True,
                        })
                
                # Process SMS usage
                sms_usage = data[
                    (data['Charges__BudgetCentre__ProductFamily__Charge__@UnitType'] == 'Ks') &
                    (data['Charges__BudgetCentre__ProductFamily__Charge__@Desc'].str.contains('SMS', na=False))
                ]
                
                for _, row in sms_usage.iterrows():
                    service_name = row['Charges__BudgetCentre__ProductFamily__Charge__@Desc']
                    count = int(float(row['Charges__BudgetCentre__ProductFamily__Charge__@Units']))
                    if pd.notna(service_name):
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': 'sms',
                            'amount': 0.0,
                            'quantity': count,
                            'unit': 'SMS',
                            'total': 0.0,
                            'is_excess_usage': False,
                        })
                
                # Process MMS usage
                mms_usage = data[
                    (data['Charges__BudgetCentre__ProductFamily__Charge__@UnitType'] == 'Ks') &
                    (data['Charges__BudgetCentre__ProductFamily__Charge__@Desc'].str.contains('MMS', na=False))
                ]
                
                for _, row in mms_usage.iterrows():
                    service_name = row['Charges__BudgetCentre__ProductFamily__Charge__@Desc']
                    count = int(float(row['Charges__BudgetCentre__ProductFamily__Charge__@Units']))
                    if pd.notna(service_name):
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': 'mms',
                            'amount': 0.0,
                            'quantity': count,
                            'unit': 'MMS',
                            'total': 0.0,
                            'is_excess_usage': False,
                        })
                
                # Process call usage
                call_usage = data[data['Charges__BudgetCentre__ProductFamily__Charge__@UnitType'] == 'sekundy']
                for _, row in call_usage.iterrows():
                    service_name = row['Charges__BudgetCentre__ProductFamily__Charge__@Desc']
                    duration = float(row['Charges__BudgetCentre__ProductFamily__Charge__@Units'])
                    if pd.notna(service_name):
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': 'voice',
                            'amount': 0.0,
                            'quantity': duration,
                            'unit': 'Second',
                            'total': 0.0,
                            'is_excess_usage': False,
                        })
                
                # Process data usage
                data_usage = data[data['Charges__BudgetCentre__ProductFamily__Charge__@UnitType'] == 'MB']
                for _, row in data_usage.iterrows():
                    service_name = row['Charges__BudgetCentre__ProductFamily__Charge__@Desc']
                    volume = float(row['Charges__BudgetCentre__ProductFamily__Charge__@Units'])
                    if pd.notna(service_name):
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': 'data',
                            'amount': 0.0,
                            'quantity': volume,
                            'unit': 'MB',
                            'total': 0.0,
                            'is_excess_usage': False,
                        })
            
        except Exception as e:
            _logger.error(f"Error processing Telekom CSV file: {str(e)}")
            raise
            
        return result
        
    def _process_o2_csv(self, csv_content):
        """Process O2 CSV file"""
        result = []
        
        try:
            # First, preprocess to fill in missing phone numbers
            # Read the CSV file into a pandas DataFrame
            df_raw = pd.read_csv(io.BytesIO(csv_content), encoding='utf-8', low_memory=False)
            
            # Find the MSISDN column
            msisdn_col = None
            for col in df_raw.columns:
                if 'MSISDN' in col:
                    msisdn_col = col
                    break
            if not msisdn_col:
                raise UserError(_("Could not find MSISDN column in O2 CSV file"))
            # Forward fill the phone numbers (fill missing values with the last valid value)
            df_raw[msisdn_col] = df_raw[msisdn_col].fillna(method='ffill')
            # Group by phone number
            grouped = df_raw.groupby(msisdn_col)
            for phone_number, data in grouped:
                if pd.isna(phone_number):
                    continue
                # Clean phone number to keep only numeric characters
                phone_number = self._clean_phone_number(phone_number)
                # Skip non-Slovak numbers
                if not phone_number.startswith('421'):
                    continue
                # Get the basic plan amount from SubscriberTotalNETAmount
                basic_plan_amount = self._safe_convert_to_float(data['Subscribers__Subscriber__SubscriberTotalNETAmount'].iloc[0])
                
                # Process recurring fees
                recurring_fees = data[
                    (data['Subscribers__Subscriber__InvoiceLines__FeeLines__Fee__FeeType'] == 'recurring_arrears') &
                    (~data['Subscribers__Subscriber__InvoiceLines__FeeLines__Fee__FeeName'].str.contains('VPN', na=False))
                ]
                
                for _, row in recurring_fees.iterrows():
                    service_name = row['Subscribers__Subscriber__InvoiceLines__FeeLines__Fee__FeeName']
                    if pd.notna(service_name):
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': 'basic',
                            'amount': basic_plan_amount,  # Use the basic plan amount from SubscriberTotalNETAmount
                            'total': basic_plan_amount,
                            'is_excess_usage': False,
                        })
                
                # Process charged fees (one-time payments and extra charges)
                charged_fees = data[
                    (data['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageItemNetAmount'] > 0) |
                    (data['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageUOM'] == 'Money')
                ]
                
                for _, row in charged_fees.iterrows():
                    service_name = row['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageItemName']
                    amount = self._safe_convert_to_float(row['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageItemNetAmount'])
                    vat = row['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__VAT']
                    
                    if pd.notna(service_name):
                        service_type = 'other'
                        if 'data' in service_name.lower():
                            service_type = 'data'
                        elif 'voice' in service_name.lower() or 'call' in service_name.lower():
                            service_type = 'voice'
                        elif 'sms' in service_name.lower():
                            service_type = 'sms'
                        elif 'mms' in service_name.lower():
                            service_type = 'mms'
                        elif 'roaming' in service_name.lower():
                            service_type = 'roaming'
                        
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': service_type,
                            'amount': amount,
                            'total': amount,
                            'is_excess_usage': True,
                        })
                
                # Process SMS/MMS usage
                sms_usage = data[
                    (data['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageUOM'] == 'occurrence') &
                    (data['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageItemName'].str.contains('SMS', na=False))
                ]
                
                mms_usage = data[
                    (data['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageUOM'] == 'occurrence') &
                    (data['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageItemName'].str.contains('MMS', na=False))
                ]
                
                for _, row in sms_usage.iterrows():
                    service_name = row['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageItemName']
                    count = int(float(row['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__Amount']))
                    if pd.notna(service_name):
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': 'sms',
                            'amount': 0.0,
                            'quantity': count,
                            'unit': 'SMS',
                            'total': 0.0,
                            'is_excess_usage': False,
                        })
                
                for _, row in mms_usage.iterrows():
                    service_name = row['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageItemName']
                    count = int(float(row['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__Amount']))
                    if pd.notna(service_name):
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': 'mms',
                            'amount': 0.0,
                            'quantity': count,
                            'unit': 'MMS',
                            'total': 0.0,
                            'is_excess_usage': False,
                        })
                
                # Process call usage
                call_usage = data[data['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageUOM'] == 'Second']
                for _, row in call_usage.iterrows():
                    service_name = row['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageItemName']
                    duration = float(row['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__Volume'])
                    if pd.notna(service_name):
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': 'voice',
                            'amount': 0.0,
                            'quantity': duration,
                            'unit': 'Second',
                            'total': 0.0,
                            'is_excess_usage': False,
                        })
                
                # Process data usage
                data_usage = data[data['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageUOM'] == 'Byte']
                for _, row in data_usage.iterrows():
                    service_name = row['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__UsageItemName']
                    volume = float(row['Subscribers__Subscriber__InvoiceLines__UsageLines__Usage__Volume'])
                    if pd.notna(service_name):
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': 'data',
                            'amount': 0.0,
                            'quantity': volume,
                            'unit': 'Byte',
                            'total': 0.0,
                            'is_excess_usage': False,
                        })
            
        except Exception as e:
            _logger.error(f"Error processing O2 CSV file: {str(e)}")
            raise
            
        return result
    
    # Legacy methods removed - only using implementations with parameters
    
    def action_done(self):
        """Mark invoice as done"""
        self.write({'state': 'done'})
    
    def action_reset_to_draft(self):
        """Reset invoice to draft"""
        self.write({'state': 'draft'})
        # Delete invoice lines
        self.invoice_line_ids.unlink()

    @api.model
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
                    # Remove any currency symbols and other non-numeric characters except decimal separators
                    cleaned_value = re.sub(r'[^\d.,]', '', value_str)
                    # Replace comma with period for decimal point
                    cleaned_value = cleaned_value.replace(',', '.')
                    # If there are multiple decimal points, keep only the last one
                    if cleaned_value.count('.') > 1:
                        parts = cleaned_value.split('.')
                        cleaned_value = ''.join(parts[:-1]) + '.' + parts[-1]
                    return float(cleaned_value)
                except ValueError as e:
                    _logger.error(f"Failed to convert value '{value_str}' to float: {str(e)}")
                    return 0.0

    @api.model
    def _clean_phone_number(self, phone_number):
        """Remove all non-numeric characters from a phone number and strip leading '00'"""
        if not phone_number:
            return ''
        # Remove all non-numeric characters
        cleaned_number = re.sub(r'\D', '', str(phone_number))
        # Strip leading '00'
        return cleaned_number.lstrip('00')


class ContractMobileInvoiceLine(models.Model):
    _name = "contract.mobile.invoice.line"
    _description = "Mobile Service Invoice Line"

    invoice_id = fields.Many2one(
        comodel_name="contract.mobile.invoice",
        string="Invoice",
        required=True,
        ondelete='cascade',
    )
    phone_number = fields.Char(string="Phone Number", required=True)
    service_name = fields.Char(string="Service Name")
    service_type = fields.Selection(
        selection=[
            ('basic', 'Basic Plan'),
            ('recurring', 'Recurring Service'),
            ('data', 'Data Usage'),
            ('voice', 'Voice Usage'),
            ('sms', 'SMS Usage'),
            ('mms', 'MMS Usage'),
            ('roaming', 'Roaming'),
            ('other', 'Other'),
        ],
        string="Service Type",
        default='other',
    )
    amount = fields.Float(string="Amount", digits=(16, 6))
    quantity = fields.Float(string="Quantity", digits=(16, 6))
    unit = fields.Char(string="Unit")
    total = fields.Float(string="Total", digits=(16, 2))
    is_excess_usage = fields.Boolean(string="Is Excess Usage", default=False)
    
    mobile_service_id = fields.Many2one(
        comodel_name="contract.mobile.service",
        string="Mobile Service",
        compute="_compute_mobile_service",
        store=True,
    )
    
    partner_id = fields.Many2one(
        related="mobile_service_id.partner_id",
        string="Partner",
        store=True,
    )
    
    @api.depends('phone_number')
    def _compute_mobile_service(self):
        """Find the related mobile service based on phone number"""
        for record in self:
            # Clean phone number to ensure consistent format
            cleaned_number = self._clean_phone_number(record.phone_number)
            # Search for mobile service with matching number
            mobile_service = self.env['contract.mobile.service'].search([
                ('phone_number', 'like', cleaned_number),
                ('is_active', '=', True),
            ], limit=1)
            record.mobile_service_id = mobile_service.id if mobile_service else False
    
    @api.model
    def _clean_phone_number(self, phone_number):
        """Remove all non-numeric characters from a phone number and strip leading '00'"""
        if not phone_number:
            return ''
        # Remove all non-numeric characters
        cleaned_number = re.sub(r'\D', '', str(phone_number))
        # Strip leading '00'
        return cleaned_number.lstrip('00')


class ContractMobileUsageReport(models.Model):
    _name = "contract.mobile.usage.report"
    _description = "Mobile Usage Report"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = "date desc"

    name = fields.Char(string="Name", required=True, tracking=True)
    date = fields.Date(string="Date", required=True, tracking=True)
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        required=True,
        tracking=True,
    )
    invoice_id = fields.Many2one(
        comodel_name="contract.mobile.invoice",
        string="Invoice",
        required=True,
        tracking=True,
    )
    operator = fields.Selection(related="invoice_id.operator", string="Operator", store=True)
    report_line_ids = fields.One2many(
        comodel_name="contract.mobile.usage.report.line",
        inverse_name="report_id",
        string="Report Lines",
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('done', 'Done'),
        ],
        string="State",
        default='draft',
        tracking=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    report_file = fields.Binary(string="Report File", attachment=True)
    report_filename = fields.Char(string="Report File Name")
    
    def action_generate_report(self):
        """Generate the usage report"""
        self.ensure_one()
        
        if self.invoice_id and self.partner_id:
            # Delete existing report lines
            self.report_line_ids.unlink()
            
            try:
                # Get invoice lines
                invoice_lines = self.invoice_id.invoice_line_ids
                
                # Group lines by phone number
                phone_groups = {}
                for line in invoice_lines:
                    if line.phone_number not in phone_groups:
                        phone_groups[line.phone_number] = {
                            'mobile_service_id': line.mobile_service_id.id if line.mobile_service_id else False,
                            'partner_name': line.partner_id.name if line.partner_id else '',
                            'basic_plan': '',
                            'basic_plan_cost': 0.0,
                            'excess_usage_cost': 0.0,
                            'total_cost': 0.0,
                            'excess_data_usage': 0.0,
                            'excess_voice_usage': 0.0,
                            'excess_sms_usage': 0.0,
                        }
                        
                    # Update group data based on line service type
                    if line.service_type == 'basic':
                        phone_groups[line.phone_number]['basic_plan'] += (line.service_name or '') + ' '
                        phone_groups[line.phone_number]['basic_plan_cost'] += line.total
                    elif line.is_excess_usage:
                        phone_groups[line.phone_number]['excess_usage_cost'] += line.total
                        
                        # Track specific usage types
                        if line.service_type == 'data':
                            phone_groups[line.phone_number]['excess_data_usage'] += line.quantity or 0.0
                        elif line.service_type == 'voice':
                            phone_groups[line.phone_number]['excess_voice_usage'] += line.quantity or 0.0
                        elif line.service_type == 'sms':
                            phone_groups[line.phone_number]['excess_sms_usage'] += line.quantity or 0.0
                            
                    # Update total cost
                    phone_groups[line.phone_number]['total_cost'] += line.total
                
                # Create report lines
                report_lines = []
                for phone_number, data in phone_groups.items():
                    # Calculate values
                    total_cost = data['basic_plan_cost'] + data['excess_usage_cost']
                    
                    report_lines.append((0, 0, {
                        'phone_number': phone_number,
                        'mobile_service_id': data['mobile_service_id'],
                        'partner_name': data['partner_name'],
                        'basic_plan': data['basic_plan'].strip(),
                        'basic_plan_cost': data['basic_plan_cost'],
                        'excess_usage_cost': data['excess_usage_cost'],
                        'total_cost': total_cost,
                        'excess_data_usage': data['excess_data_usage'],
                        'excess_voice_usage': data['excess_voice_usage'],
                        'excess_sms_usage': data['excess_sms_usage'],
                    }))
                
                # Generate Excel report
                report_file, report_filename = self._generate_excel_report(phone_groups)
                
                # Update report with lines and file
                self.write({
                    'report_line_ids': report_lines,
                    'report_file': report_file,
                    'report_filename': report_filename,
                    'state': 'done',
                })
                
                return True
                
            except Exception as e:
                _logger.error(f"Error generating report: {str(e)}")
                raise UserError(_("Error generating report: %s") % str(e))
                
        return False
    
    def _generate_excel_report(self, phone_groups):
        """Generate an Excel report file"""
        # This would typically use a library like xlsxwriter or openpyxl
        # For now, we'll create a simple CSV file using pandas
        
        # Create a DataFrame from the phone groups data
        data = []
        for phone_number, info in phone_groups.items():
            data.append({
                'Phone Number': phone_number,
                'Basic Plan': info['basic_plan'],
                'Basic Plan Cost': info['basic_plan_cost'],
                'Excess Usage Cost': info['excess_usage_cost'],
                'Total Cost': info['total_cost'],
                'Excess Data Usage': info['excess_data_usage'],
                'Excess Voice Usage': info['excess_voice_usage'],
                'Excess SMS Usage': info['excess_sms_usage'],
            })
            
        df = pd.DataFrame(data)
        
        # Create CSV in memory
        output = io.StringIO()
        df.to_csv(output, index=False)
        
        # Convert to base64
        report_content = base64.b64encode(output.getvalue().encode('utf-8'))
        report_filename = f"usage_report_{self.partner_id.name}_{self.date}.csv"
        
        return report_content, report_filename


class ContractMobileUsageReportLine(models.Model):
    _name = "contract.mobile.usage.report.line"
    _description = "Mobile Usage Report Line"

    report_id = fields.Many2one(
        comodel_name="contract.mobile.usage.report",
        string="Report",
        required=True,
        ondelete='cascade',
    )
    phone_number = fields.Char(string="Phone Number", required=True)
    mobile_service_id = fields.Many2one(
        comodel_name="contract.mobile.service",
        string="Mobile Service",
    )
    partner_name = fields.Char(string="Partner Name")
    basic_plan = fields.Char(string="Basic Plan")
    basic_plan_cost = fields.Float(string="Basic Plan Cost", digits=(16, 2))
    excess_usage_cost = fields.Float(string="Excess Usage Cost", digits=(16, 2))
    total_cost = fields.Float(string="Total Cost", digits=(16, 2))
    excess_data_usage = fields.Float(string="Excess Data Usage", digits=(16, 6))
    excess_voice_usage = fields.Float(string="Excess Voice Usage", digits=(16, 6))
    excess_sms_usage = fields.Float(string="Excess SMS Usage", digits=(16, 0))
