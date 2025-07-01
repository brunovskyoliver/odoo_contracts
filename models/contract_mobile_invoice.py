# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import datetime
from dateutil.relativedelta import relativedelta
import os
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image
import pandas as pd
import re
import io
import base64
import logging

_logger = logging.getLogger(__name__)

def insert_image(sheet, image_path, cell):
    """Insert an image into the specified cell of the sheet."""
    try:
        # Load the image
        img = Image(image_path)
        
        # Adjust the size of the image if needed
        img.width, img.height = 200, 100
        
        # Extract column letter and row number from cell reference
        col_letter = ''.join(filter(str.isalpha, cell))
        row_num = int(''.join(filter(str.isdigit, cell)))
        
        # Get column width in pixels (1 unit = 7 pixels)
        col_width = sheet.column_dimensions[col_letter].width
        col_width_px = col_width * 7
        
        # Calculate offsets to center the image
        x_offset = (col_width_px - img.width) / 2
        
        # Adjust row height to fit the image
        row_height = img.height * 0.75  # Convert to points (Excel units)
        sheet.row_dimensions[row_num].height = row_height
        
        # Calculate y_offset to center vertically
        y_offset = (row_height - img.height * 0.75) / 2
        
        # Add the image with calculated offsets
        img.anchor = cell
        img.left = x_offset
        img.top = y_offset
        
        sheet.add_image(img)
        
    except Exception as e:
        _logger.error(f"Error inserting image: {e}")

def format_duration(seconds):
    """Format seconds into HH:MM:SS format."""
    try:
        seconds = float(seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    except ValueError:
        return "00:00:00"

def format_data_usage(bytes_or_mb):
    """Format bytes or MB into appropriate unit (MB/GB)."""
    try:
        # Convert the input to float and handle MB conversion
        value = float(bytes_or_mb)
        mb = value / (1024 * 1024) if value > 1024 * 1024 else value
        
        # Format as GB if over 1024 MB, otherwise as MB
        if mb > 1024:
            return f"{mb/1024:.2f} GB"
        else:
            return f"{mb:.2f} MB"
    except ValueError:
        return "0 MB"

def get_output_folder(customer_info: tuple, base_dir: str = "output") -> str:
    """Determine the appropriate output folder based on customer info."""
    customer_type, name = customer_info
    
    # Create base output directory if it doesn't exist
    os.makedirs(base_dir, exist_ok=True)
    
    # Create companies and individuals directories
    companies_dir = os.path.join(base_dir, "companies")
    individuals_dir = os.path.join(base_dir, "individuals")
    os.makedirs(companies_dir, exist_ok=True)
    os.makedirs(individuals_dir, exist_ok=True)
    
    if customer_type == 'company':
        company_dir = os.path.join(companies_dir, name)
        os.makedirs(company_dir, exist_ok=True)
        return company_dir
    else:  # 'individual' or 'unknown'
        return individuals_dir


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
                    vat_rate = str(row['Charges__BudgetCentre__ProductFamily__Charge__@VatRate']) + "%"
                    
                    if pd.notna(service_name):
                        # Map T-Biznis service names to NOVEM names
                        service_name = format_plan_name(service_name)
                        
                        result.append({
                            'phone_number': phone_number,
                            'service_name': service_name,
                            'service_type': 'basic',
                            'amount': price,
                            'total': price,
                            'is_excess_usage': False,
                            'vat': vat_rate,
                        })
                
                # Process paid services (excluding T-Biznis)
                paid_services = data[
                    (data['Charges__BudgetCentre__ProductFamily__Charge__@Price'] > 0) &
                    (~data['Charges__BudgetCentre__ProductFamily__Charge__@Desc'].str.contains('T-Biznis', na=False))
                ]
                
                for _, row in paid_services.iterrows():
                    service_name = row['Charges__BudgetCentre__ProductFamily__Charge__@Desc']
                    price = self._safe_convert_to_float(row['Charges__BudgetCentre__ProductFamily__Charge__@Price'])
                    vat_rate = str(row['Charges__BudgetCentre__ProductFamily__Charge__@VatRate']) + "%"

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
                            'vat': vat_rate,
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
                            'vat': vat,
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
        """Mark invoice as done and process excess usage"""
        self.ensure_one()
        
        # First mark as done
        self.write({'state': 'done'})
        
        # Get all invoice lines with excess usage, limited to specific partner for testing
        excess_usage_by_partner = {}
        test_partner_id = 1074  # Testing with specific partner
        
        for line in self.invoice_line_ids.filtered(
            lambda l: l.is_excess_usage and 
            l.partner_id and 
            l.partner_id.id == test_partner_id
        ):
            if line.partner_id.id not in excess_usage_by_partner:
                excess_usage_by_partner[line.partner_id.id] = {
                    'total_23': 0.0,  # For 23% VAT
                    'total_0': 0.0,   # For 0% VAT
                    'partner': line.partner_id,
                }
            
            # Sum amounts based on VAT rate
            if line.vat == '23%':
                excess_usage_by_partner[line.partner_id.id]['total_23'] += line.total
            else:  # Assume 0% for all other cases
                excess_usage_by_partner[line.partner_id.id]['total_0'] += line.total

        _logger.info(f"Processing excess usage for partner ID {test_partner_id}")
        
        # Process each partner's excess usage
        for partner_data in excess_usage_by_partner.values():
            # Skip if no excess usage in either VAT rate
            if partner_data['total_23'] <= 0 and partner_data['total_0'] <= 0:
                continue
                
            _logger.info(f"Found excess usage totals - 23% VAT: {partner_data['total_23']}, 0% VAT: {partner_data['total_0']} for partner {partner_data['partner'].name}")
            
            # Find the mobilky contract for this partner
            contract = self.env['contract.contract'].search([
                ('partner_id', '=', partner_data['partner'].id),
                ('x_contract_type', '=', 'Mobilky')
            ], limit=1)
            
            if not contract:
                _logger.info(f"No active Mobilky contract found for partner {partner_data['partner'].name}")
                continue
                
            _logger.info(f"Found contract: {contract.name}")
            
            # Get products for both VAT rates
            product_23 = self.env['product.product'].search([
                ('display_name', '=', 'Vyúčtovanie paušálnych služieb a spotreby HLAS 23%')
            ], limit=1)
            
            product_0 = self.env['product.product'].search([
                ('display_name', '=', 'Vyúčtovanie paušálnych služieb a spotreby HLAS 0%')
            ], limit=1)
            
            if not (product_23 and product_0):
                _logger.error("Could not find both Vyúčtovanie products (23% and 0%)")
                continue
            
            # Handle 23% VAT line
            if partner_data['total_23'] > 0:
                existing_line_23 = contract.contract_line_ids.filtered(
                    lambda l: l.product_id == product_23
                )
                
                if existing_line_23:
                    new_total = existing_line_23.price_unit + partner_data['total_23']
                    _logger.info(f"Updating existing 23% VAT line - current: {existing_line_23.price_unit}, adding: {partner_data['total_23']}, new total: {new_total}")
                    
                    existing_line_23.write({
                        'price_unit': new_total,  # Adjust for 23% VAT
                        'x_zlavnena_cena': new_total,  # Adjust for 23% VAT
                        'date_start': contract.recurring_next_date,
                        'recurring_next_date': contract.recurring_next_date
                    })
                else:
                    _logger.info(f"Creating new 23% VAT line with amount: {partner_data['total_23']}")
                    self.env['contract.line'].with_context(skip_date_check=True).create({
                        'contract_id': contract.id,
                        'product_id': product_23.id,
                        'name': 'Vyúčtovanie paušálnych služieb a spotreby HLAS 23%',
                        'quantity': 1,
                        'price_unit': partner_data['total_23'],  # Adjust for 23% VAT
                        'recurring_rule_type': 'monthly',
                        'recurring_interval': 1,
                        "uom_id": 1,
                        "x_zlavnena_cena": partner_data['total_23'],  # Adjust for 23% VAT
                        'date_start': contract.recurring_next_date,
                        'recurring_next_date': contract.recurring_next_date,
                        'is_auto_renew': False,
                    })
            
            # Handle 0% VAT line
            if partner_data['total_0'] > 0:
                existing_line_0 = contract.contract_line_ids.filtered(
                    lambda l: l.product_id == product_0
                )
                
                if existing_line_0:
                    new_total = existing_line_0.price_unit + partner_data['total_0']
                    _logger.info(f"Updating existing 0% VAT line - current: {existing_line_0.price_unit}, adding: {partner_data['total_0']}, new total: {new_total}")
                    
                    existing_line_0.write({
                        'price_unit': new_total,
                        'x_zlavnena_cena': new_total,
                        'date_start': contract.recurring_next_date,
                        'recurring_next_date': contract.recurring_next_date
                    })
                else:
                    _logger.info(f"Creating new 0% VAT line with amount: {partner_data['total_0']}")
                    self.env['contract.line'].with_context(skip_date_check=True).create({
                        'contract_id': contract.id,
                        'product_id': product_0.id,
                        'name': 'Vyúčtovanie paušálnych služieb a spotreby HLAS 0%',
                        'quantity': 1,
                        'price_unit': partner_data['total_0'],
                        'recurring_rule_type': 'monthly',
                        'recurring_interval': 1,
                        "uom_id": 1,
                        "x_zlavnena_cena": partner_data['total_0'],
                        'date_start': contract.recurring_next_date,
                        'recurring_next_date': contract.recurring_next_date,
                        'is_auto_renew': False,
                    })
                
        return True

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
    
    @api.model
    def reset_excess_usage_lines(self):
        """Reset all excess usage contract lines on the 1st of each month"""
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
    vat = fields.Char(string="DPH") # TODO: Add VAT to the invoice line
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
                invoice_lines = self.invoice_id.invoice_line_ids.filtered(
                    lambda l: l.partner_id == self.partner_id
                )
                
                # Group lines by phone number
                phone_groups = {}
                for line in invoice_lines:
                    # Initialize phone group if not exists
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
                            'is_company': line.partner_id.is_company if line.partner_id else False,
                        }
                    
                    # Update group data based on line type
                    data = phone_groups[line.phone_number]
                    if line.service_type == 'basic':
                        data['basic_plan'] = line.service_name
                        data['basic_plan_cost'] = line.total
                    elif line.service_type == 'data':
                        data['excess_data_usage'] += line.quantity
                    elif line.service_type == 'voice':
                        data['excess_voice_usage'] += line.quantity
                    elif line.service_type in ['sms', 'mms']:
                        data['excess_sms_usage'] += line.quantity
                    
                    if line.is_excess_usage:
                        data['excess_usage_cost'] += line.total
                    data['total_cost'] += line.total
                
                # Process each phone number
                for phone_number, data in phone_groups.items():
                    # Create report line
                    self.env['contract.mobile.usage.report.line'].create({
                        'report_id': self.id,
                        'phone_number': phone_number,
                        'mobile_service_id': data['mobile_service_id'],
                        'partner_name': data['partner_name'],
                        'basic_plan': data['basic_plan'],
                        'basic_plan_cost': data['basic_plan_cost'],
                        'excess_usage_cost': data['excess_usage_cost'],
                        'total_cost': data['total_cost'],
                        'excess_data_usage': data['excess_data_usage'],
                        'excess_voice_usage': data['excess_voice_usage'],
                        'excess_sms_usage': data['excess_sms_usage'],
                    })
                
                # Generate Excel report
                report_content = self._generate_excel_report(phone_groups)
                if report_content:
                    self.write({
                        'report_file': report_content,
                        'report_filename': f"{self.name}_{self.date}.xlsx",
                        'state': 'done'
                    })
                
                return True
                
            except Exception as e:
                _logger.error(f"Error generating report: {str(e)}")
                raise UserError(_("Error generating report: %s") % str(e))
        
        return False

    def _generate_excel_report(self, phone_groups):
        """Generate an Excel report file for each partner, containing all their phone numbers.
        Uses the same formatting and sections as nadspotreba.py."""
        # Get all invoice lines for the partner
        invoice_lines = self.invoice_id.invoice_line_ids.filtered(
            lambda l: l.partner_id == self.partner_id
        )
        try:
            base_path = os.path.dirname(os.path.abspath(__file__))
            novem_logo = os.path.join(base_path, '..', 'novem.png')
            
            # Group phone numbers by partner
            partner_groups = {}
            for phone_number, data in phone_groups.items():
                # Format phone number consistently
                formatted_phone = format_phone_number(phone_number)
                partner_name = data.get('partner_name', 'Unknown')
                partner_type = 'company' if data.get('is_company') else 'individual'
                
                key = (partner_type, partner_name)
                if key not in partner_groups:
                    partner_groups[key] = []
                # Store the formatted phone number
                data['formatted_phone'] = formatted_phone
                _logger.info(f"Processing phone number: {formatted_phone} for partner: {partner_name} ({partner_type})")
                # Format basic plan name
                data['formatted_plan'] = format_plan_name(data.get('basic_plan', ''))
                partner_groups[key].append((formatted_phone, data))

            # Continue with workbook creation...
            wb = Workbook()
            ws = wb.active
            
            # Add NOVEM logo if exists
            if os.path.exists(novem_logo):
                insert_image(ws, novem_logo, "A1")
            
            current_row = 5  # Start after logo
            
            thin_border = Border(
                left=Side(style='thin'),
                right=Side(style='thin'),
                top=Side(style='thin'),
                bottom=Side(style='thin')
            )

            # Process each partner's data
            for partner_info, partner_data in partner_groups.items():
                for phone_number, data in sorted(partner_data):  # Sort by phone number
                    # Header section
                    ws.cell(row=current_row, column=1, value="Rozpis spotreby pre telefónne číslo:").font = Font(bold=True, color="438EAC")
                    ws.cell(row=current_row, column=2, value=data['formatted_phone'])  # Use formatted phone number
                    current_row += 1

                    # Basic plan section
                    _logger.info(f"Adding basic plan: {data['formatted_plan']}")
                    ws.cell(row=current_row, column=1, value=data['formatted_plan'])  # Use formatted plan name
                    current_row += 2
                    
                    # Rozpis účtovaných poplatkov
                    # Get all excessive usage lines for this phone number
                    excessive_lines = [line for line in invoice_lines if 
                                    line.phone_number == phone_number and 
                                    line.is_excess_usage and 
                                    line.total > 0]
                    
                    if excessive_lines:
                        ws.cell(row=current_row, column=1, value="Rozpis účtovaných poplatkov:").font = Font(bold=True)
                        ws.cell(row=current_row, column=2, value="DPH").font = Font(bold=True)
                        ws.cell(row=current_row, column=3, value="Suma v EUR bez DPH").font = Font(bold=True)
                        for cell in [ws.cell(row=current_row, column=i) for i in range(1, 4)]:
                            cell.border = thin_border
                        current_row += 1

                        # List each excessive usage line separately
                        for line in excessive_lines:
                            ws.cell(row=current_row, column=1, value=line.service_name)
                            ws.cell(row=current_row, column=2, value=line.vat if line.vat else "0%")
                            x = f"{float(line.total):.4f}".replace('.', ',')
                            cell = ws.cell(row=current_row, column=3, value=x)
                            cell.number_format = '0.0000'
                            for cell in [ws.cell(row=current_row, column=i) for i in range(1, 4)]:
                                cell.border = thin_border
                            current_row += 1
                        current_row += 1

                    # Rozpis SMS / MMS
                    # Get SMS/MMS, voice, and data usage lines for this phone number
                    sms_lines = [line for line in invoice_lines if 
                              line.phone_number == phone_number and 
                              line.service_type in ['sms', 'mms']]
                    voice_lines = [line for line in invoice_lines if 
                                line.phone_number == phone_number and 
                                line.service_type == 'voice']
                    data_lines = [line for line in invoice_lines if 
                               line.phone_number == phone_number and 
                               line.service_type == 'data']

                    # Rozpis SMS / MMS
                    if sms_lines:
                        ws.cell(row=current_row, column=1, value="Rozpis SMS / MMS:").font = Font(bold=True)
                        ws.cell(row=current_row, column=2, value="Počet kusov").font = Font(bold=True)
                        for cell in [ws.cell(row=current_row, column=i) for i in range(1, 3)]:
                            cell.border = thin_border
                        current_row += 1
                        
                        for line in sms_lines:
                            ws.cell(row=current_row, column=1, value=line.service_name)
                            ws.cell(row=current_row, column=2, value=f"{int(line.quantity)} ks")
                            for cell in [ws.cell(row=current_row, column=i) for i in range(1, 3)]:
                                cell.border = thin_border
                            current_row += 1
                        current_row += 1

                    # Rozpis volaní
                    if voice_lines:
                        ws.cell(row=current_row, column=1, value="Rozpis volaní:").font = Font(bold=True)
                        ws.cell(row=current_row, column=2, value="Trvanie hovorov").font = Font(bold=True)
                        for cell in [ws.cell(row=current_row, column=i) for i in range(1, 3)]:
                            cell.border = thin_border
                        current_row += 1
                        
                        for line in voice_lines:
                            ws.cell(row=current_row, column=1, value=line.service_name)
                            duration = format_duration(line.quantity)
                            ws.cell(row=current_row, column=2, value=duration)
                            for cell in [ws.cell(row=current_row, column=i) for i in range(1, 3)]:
                                cell.border = thin_border
                            current_row += 1
                        current_row += 1

                    # Rozpis dát
                    if data_lines:
                        ws.cell(row=current_row, column=1, value="Rozpis dát:").font = Font(bold=True)
                        ws.cell(row=current_row, column=2, value="Spotreba dát").font = Font(bold=True)
                        for cell in [ws.cell(row=current_row, column=i) for i in range(1, 3)]:
                            cell.border = thin_border
                        current_row += 1
                        
                        for line in data_lines:
                            ws.cell(row=current_row, column=1, value=line.service_name)
                            formatted_usage = format_data_usage(line.quantity)
                            ws.cell(row=current_row, column=2, value=formatted_usage)
                            for cell in [ws.cell(row=current_row, column=i) for i in range(1, 3)]:
                                cell.border = thin_border
                            current_row += 1
                        current_row += 1

                    # Total section - only excess charges, not basic plan
                    excess_total = sum(line.total for line in invoice_lines if 
                                    line.phone_number == phone_number and 
                                    line.is_excess_usage and 
                                    line.service_type != 'basic')
                    
                    ws.cell(row=current_row, column=1, value=f"Faktúrovaná suma nad paušál bez DPH:").font = Font(bold=True)
                    x = f"{float(excess_total):.4f}".replace('.', ',')
                    cell = ws.cell(row=current_row, column=2, value=x)
                    cell.number_format = '0.0000'
                    for cell in [ws.cell(row=current_row, column=i) for i in range(1, 3)]:
                        cell.border = thin_border
                    current_row += 3  # Extra space between phone numbers

                # Adjust column widths
                for col in ws.columns:
                    max_length = 0
                    column = get_column_letter(col[0].column)
                    for cell in col:
                        try:
                            if cell.value:
                                max_length = max(max_length, len(str(cell.value)))
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 100)
                    ws.column_dimensions[column].width = adjusted_width

                # Save workbook to memory
                output = io.BytesIO()
                wb.save(output)
                output.seek(0)
                report_content = base64.b64encode(output.getvalue())
                return report_content # Return the first report since we're generating for a specific partner

        except Exception as e:
            _logger.error(f"Error generating Excel report: {str(e)}")
            return False
class ContractMobileUsageReportLine(models.Model):
    _name = "contract.mobile.usage.report.line"
    _description = "Mobile Usage Report Line"

    report_id = fields.Many2one(
        comodel_name="contract.mobile.usage.report",
        string="Report",
        required=True,
        ondelete='cascade',
        index=True,
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


def format_duration(seconds):
    """Format duration in seconds to HH:MM:SS"""
    try:
        seconds = float(seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02}:{minutes:02}:{secs:02}"
    except Exception as e:
        _logger.error(f"Error formatting duration: {str(e)}")
        return "00:00:00"

# Note: Using the format_data_usage function defined at the top of the file
def format_data_usage_redundant(volume_mb):
    """This function is deprecated. Use the format_data_usage function at the top of the file instead."""
    return format_data_usage(volume_mb)

def format_phone_number(number):
    """Format phone number to 421xxxxxxxxx format"""
    if not number:
        return ''
    # Remove all non-numeric characters
    cleaned = re.sub(r'\D', '', str(number))
    # Remove leading '00'
    cleaned = cleaned.lstrip('00')
    # If number starts with '0', replace it with '421'
    if cleaned.startswith('0'):
        cleaned = '421' + cleaned[1:]
    # If number doesn't start with '421', add it
    if not cleaned.startswith('421'):
        cleaned = '421' + cleaned
    return cleaned

def format_plan_name(text):
    """Convert T-Biznis plan names to NOVEM equivalents"""
    if not text:
        return ''
        
    replacements = {
        "T-Biznis Flex - Variant 1": "NOVEM nekonečno 6GB",
        "T-Biznis Flex - Variant 10": "NOVEM nekonečno 10GB",
        "T-Biznis Flex - Variant 11": "NOVEM nekonečno 30GB",
        "T-Biznis Flex - Variant 2": "NOVEM Fér bez dát",
        "T-Biznis Flex - Variant 3": "NOVEM nekonečno 20GB",
        "T-Biznis Flex - Variant 4": "NOVEM nekonečno 50GB",
        "T-Biznis Flex - Variant 5": "NOVEM 250 0,5GB",
        "T-Biznis Flex - Variant 6": "NOVEM nekonečno 0,5GB",
        "T-Biznis Flex - Variant 7": "NOVEM 250 30 GB",
        "T-Biznis Flex - Variant 8": "NOVEM 250 10 GB",
        "T-Biznis Flex - Variant 9": "NOVEM nekonečno bez dát"
    }
    
    # First, try exact match to avoid partial replacements
    result = text.strip()
    if result in replacements:
        return replacements[result]
    
    # If no exact match, try partial match but ensure full word matching
    for old, new in replacements.items():
        if old in result:
            result = result.replace(old, new)
            # Clean up any trailing digits that might have been left
            result = re.sub(r'(\d+GB)\d+', r'\1', result)
    
    if "e-Net" in result:
        result = result.replace("e-Net", "NOVEM")
        result = result.replace("minút ", "")
        result = result.replace("minut ", "")
    
    return result
