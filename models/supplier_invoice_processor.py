# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
import base64
import re
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

try:
    import PyPDF2
    import pdfplumber
except ImportError:
    _logger.warning("PyPDF2 or pdfplumber not installed. PDF processing will not work.")
    PyPDF2 = None
    pdfplumber = None


class SupplierInvoiceProcessor(models.Model):
    _name = 'supplier.invoice.processor'
    _description = 'Processor 3000'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'mail.alias.mixin']
    _order = 'create_date desc'

    def _get_alias_model_name(self, vals):
        """Return the model name for the alias"""
        return 'supplier.invoice.processor'

    def _alias_get_creation_values(self):
        """Return values to create the alias"""
        values = super()._alias_get_creation_values()
        values['alias_model_id'] = self.env['ir.model']._get('supplier.invoice.processor').id
        if self.id:
            values['alias_force_thread_id'] = self.id
        values['alias_parent_model_id'] = self.env['ir.model']._get('supplier.invoice.processor').id
        values['alias_parent_thread_id'] = self.id
        return values

    name = fields.Char(
        string='Referencia',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
        tracking=True,
    )
    
    state = fields.Selection([
        ('draft', 'Koncept'),
        ('processing', 'Spracovávanie'),
        ('pairing', 'Vyžaduje párovanie'),
        ('extracted', 'Údaje extrahované'),
        ('done', 'Faktúra vytvorená'),
        ('error', 'Chyba'),
    ], string='Status', default='draft', required=True, tracking=True)

    # Direct PDF upload field
    pdf_file = fields.Binary(
        string='Nahrať PDF',
        attachment=True,
        help='Nahrajte PDF faktúru dodávateľa na spracovanie',
    )
    
    pdf_filename = fields.Char(
        string='Názov PDF súboru',
    )
    
    attachment_id = fields.Many2one(
        'ir.attachment',
        string='PDF Príloha',
        tracking=True,
    )
    
    filename = fields.Char(
        string='Názov súboru',
        compute='_compute_filename',
        readonly=True,
    )
    
    # Extracted invoice header data
    supplier_id = fields.Many2one(
        'res.partner',
        string='Dodávateľ',
        domain=[('supplier_rank', '>', 0)],
        tracking=True,
    )
    
    invoice_number = fields.Char(
        string='Číslo faktúry',
        tracking=True,
    )
    
    invoice_date = fields.Date(
        string='Dátum faktúry',
        tracking=True,
    )
    
    invoice_due_date = fields.Date(
        string='Dátum splatnosti',
        tracking=True,
    )
    
    is_refund = fields.Boolean(
        string='Je to dobropis',
        default=False,
        help='True if this is a credit note/refund (Opravný daňový doklad)',
    )
    
    currency_id = fields.Many2one(
        'res.currency',
        string='Mena',
        default=lambda self: self.env.company.currency_id,
        tracking=True,
    )
    
    # Extracted totals
    total_untaxed = fields.Monetary(
        string='Celkom bez DPH',
        currency_field='currency_id',
        readonly=True,
    )
    
    total_tax = fields.Monetary(
        string='Celková DPH',
        currency_field='currency_id',
        readonly=True,
    )
    
    total_amount = fields.Monetary(
        string='Celková suma',
        currency_field='currency_id',
        readonly=True,
    )
    
    # Extracted text for reference
    extracted_text = fields.Text(
        string='Extrahovaný text',
        readonly=True,
    )
    
    # Invoice lines
    line_ids = fields.One2many(
        'supplier.invoice.processor.line',
        'processor_id',
        string='Invoice Lines',
    )
    
    # Created invoice
    invoice_id = fields.Many2one(
        'account.move',
        string='Vytvorená faktúra',
        readonly=True,
        tracking=True,
    )
    
    company_id = fields.Many2one(
        'res.company',
        string='Spoločnosť',
        default=lambda self: self.env.company,
        required=True,
    )
    
    default_account_id = fields.Many2one(
        'account.account',
        string='Predvolené konto',
        help='Predvolené konto na výdaje, ktoré sa bude používať pre faktúry dodávateľov ak nie je špecifické konto nájdené',
        domain=[('account_type', '=', 'expense')],
    )
    
    notes = fields.Text(
        string='Poznámky',
    )
    
    error_message = fields.Text(
        string='Chybová správa',
        readonly=True,
    )

    @api.depends('attachment_id', 'pdf_filename')
    def _compute_filename(self):
        for record in self:
            if record.attachment_id:
                record.filename = record.attachment_id.name
            elif record.pdf_filename:
                record.filename = record.pdf_filename
            else:
                record.filename = False

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code('supplier.invoice.processor') or _('New')
        
        # Create attachment from binary field if provided
        if vals.get('pdf_file') and vals.get('pdf_filename'):
            attachment = self.env['ir.attachment'].create({
                'name': vals['pdf_filename'],
                'type': 'binary',
                'datas': vals['pdf_file'],
                'mimetype': 'application/pdf',
            })
            vals['attachment_id'] = attachment.id
        
        return super().create(vals)
    
    def write(self, vals):
        # Create or update attachment from binary field if provided
        if vals.get('pdf_file'):
            filename = vals.get('pdf_filename') or self.pdf_filename or 'invoice.pdf'
            
            if self.attachment_id:
                # Update existing attachment
                self.attachment_id.write({
                    'name': filename,
                    'datas': vals['pdf_file'],
                })
            else:
                # Create new attachment
                attachment = self.env['ir.attachment'].create({
                    'name': filename,
                    'type': 'binary',
                    'datas': vals['pdf_file'],
                    'mimetype': 'application/pdf',
                })
                vals['attachment_id'] = attachment.id
        
        return super().write(vals)

    def action_process_pdf(self):
        """Extract data from PDF attachment"""
        self.ensure_one()
        
        if not self.attachment_id:
            raise UserError(_('Please attach a PDF file first.'))
        
        self.state = 'processing'
        
        try:
            # Get PDF data
            pdf_data = base64.b64decode(self.attachment_id.datas)
            
            # Extract text from first page initially (for O2 and Telekom detection)
            extracted_text = self._extract_text_from_pdf(pdf_data, first_page_only=True)
            
            # Quick check: is this an O2 or Telekom invoice? (these only need first page)
            is_o2_preliminary = 'o2 slovakia' in extracted_text.lower() or 'o2 sk' in extracted_text.lower()
            is_telekom_preliminary = 'slovak telekom' in extracted_text.lower()
            
            # If not O2 or Telekom, extract all pages instead
            if not is_o2_preliminary and not is_telekom_preliminary:
                extracted_text = self._extract_text_from_pdf(pdf_data, first_page_only=False)
            
            self.extracted_text = extracted_text
            
            # Parse invoice data
            invoice_data = self._parse_invoice_data(extracted_text, pdf_data)
            
            # Check if invoice number already exists (before updating)
            if invoice_data.get('invoice_number'):
                existing = self.search([
                    ('invoice_number', '=', invoice_data['invoice_number']),
                    ('id', '!=', self.id),  # Exclude current record
                    ('state', '!=', 'error'),  # Ignore error records
                ])
                if existing:
                    self._send_error_notification_email(invoice_data['invoice_number'])
                    raise UserError(
                        _('Faktúra č. %s už bola importovaná.\nExistujúci záznam: %s (ID: %s)') % (
                            invoice_data['invoice_number'],
                            existing[0].name,
                            existing[0].id,
                        )
                    )
            
            # Update header fields
            # Set Alza as supplier if detected
            if invoice_data.get('supplier_id'):
                self.supplier_id = invoice_data['supplier_id']
            elif invoice_data.get('supplier_vat'):
                supplier = self._find_or_create_supplier(invoice_data['supplier_vat'], invoice_data.get('supplier_name'))
                if supplier:
                    self.supplier_id = supplier.id
            
            self.is_refund = invoice_data.get('is_refund', False)
            self.invoice_number = invoice_data.get('invoice_number')
            self.invoice_date = invoice_data.get('invoice_date')
            self.invoice_due_date = invoice_data.get('invoice_due_date')
            self.total_untaxed = invoice_data.get('total_untaxed', 0.0)
            self.total_tax = invoice_data.get('total_tax', 0.0)
            self.total_amount = invoice_data.get('total_amount', 0.0)
            
            # Create invoice lines
            self.line_ids.unlink()  # Clear existing lines
            for line_data in invoice_data.get('lines', []):
                product_id, pack_qty = self._find_product(
                    line_data.get('description'), 
                    self.supplier_id.id if self.supplier_id else None
                )
                # Multiply quantity by pack_qty and divide price by pack_qty to keep same total
                quantity = line_data.get('quantity', 1.0) * pack_qty
                price_unit = line_data.get('price_unit', 0.0)
                if pack_qty > 1:
                    price_unit = price_unit / pack_qty
                
                self.env['supplier.invoice.processor.line'].create({
                    'processor_id': self.id,
                    'name': line_data.get('description'),
                    'quantity': quantity,
                    'price_unit': price_unit,
                    'vat_rate': line_data.get('vat_rate', 0.0),
                    'product_id': product_id,
                })

            # Set state based on matching status
            self._update_pairing_state()
            self.message_post(body=_('PDF načítalo %s riadkov.') % len(self.line_ids))
            
        except Exception as e:
            _logger.exception("Error processing PDF: %s", str(e))
            self.state = 'error'
            self.error_message = str(e)
            raise UserError(_('Error processing PDF: %s') % str(e))
        
        return True

    def _extract_text_from_pdf(self, pdf_data, first_page_only=False):
        """Extract text from PDF using pdfplumber or PyPDF2
        
        Args:
            pdf_data: Binary PDF data
            first_page_only: If True, extract only first page (for O2). If False, extract all pages.
        """
        if not pdf_data:
            raise UserError(_('No PDF data found.'))
        
        text = ""
        
        if pdfplumber:
            try:
                import io
                pdf_file = io.BytesIO(pdf_data)
                with pdfplumber.open(pdf_file) as pdf:
                    if first_page_only:
                        # Extract only first page
                        if len(pdf.pages) > 0:
                            page_text = pdf.pages[0].extract_text()
                            if page_text:
                                text = page_text
                    else:
                        # Extract all pages
                        for page in pdf.pages:
                            page_text = page.extract_text()
                            if page_text:
                                text += page_text
                if text:
                    return text
            except Exception as e:
                _logger.warning("pdfplumber extraction failed: %s", str(e))
        
        if PyPDF2:
            try:
                import io
                pdf_file = io.BytesIO(pdf_data)
                pdf_reader = PyPDF2.PdfReader(pdf_file)
                if first_page_only:
                    # Extract only first page
                    if len(pdf_reader.pages) > 0:
                        page_text = pdf_reader.pages[0].extract_text()
                        if page_text:
                            text = page_text
                else:
                    # Extract all pages
                    for page in pdf_reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text
                if text:
                    return text
            except Exception as e:
                _logger.warning("PyPDF2 extraction failed: %s", str(e))
        
        if not text:
            raise UserError(_('PDF text extraction failed. Please install PyPDF2 or pdfplumber, or the PDF may be image-based and requires OCR.'))

    def _update_pairing_state(self):
        """Set processor state to pairing if any lines lack a product."""
        self.ensure_one()

        if self.state == 'done':
            return

        # Skip pairing requirement for refunds - they should auto-create
        if self.is_refund:
            if self.state in ('processing', 'pairing', 'draft', 'extracted'):
                self.state = 'extracted'
            return

        # Skip pairing requirement for Va-Mont Finance - always auto-create without product matching
        if self.supplier_id and (self.supplier_id.id == 1179 or self.supplier_id.id == 1662 or self.supplier_id.id == 1653):  # Va-Mont Finance supplier ID
            if self.state in ('processing', 'pairing', 'draft', 'extracted'):
                self.state = 'extracted'
            return

        has_unmatched = any(not line.product_id for line in self.line_ids)
        if has_unmatched:
            self.state = 'pairing'
        else:
            # Only move to extracted if we're not already further along
            if self.state in ('processing', 'pairing', 'draft', 'extracted'):
                self.state = 'extracted'

    def _parse_invoice_data(self, text, pdf_data):
        """
        Parse invoice data from extracted text.
        This is a basic implementation - you may need to customize based on your invoice format.
        """
        if not text:
            raise UserError(_('No text could be extracted from the PDF. The PDF may be image-based and requires OCR.'))
        
        # Check if this is an Alza invoice
        is_alza = 'Predávajúci: Alza.sk' in text or 'Alza.sk' in text
        is_westech = 'westech' in text.lower()
        is_tes = 'tes - slovakia' in text.lower()
        is_tss = 'tss' in text.lower()
        is_asbis = 'info@asbis.sk' in text.lower()
        is_upc = 'upc broadband' in text.lower() or 'upc slovakia' in text.lower()
        is_vamont = 'va-mont' in text.lower() or 'vamont' in text.lower()
        is_telekom = 'slovak telekom' in text.lower()
        is_o2 = 'o2 slovakia' in text.lower() or 'o2 sk' in text.lower()

        
        if not is_alza and not is_westech and not is_tes and not is_tss and not is_asbis and not is_upc and not is_vamont and not is_telekom and not is_o2:
            raise UserError(_('This processor only handles Alza.sk, Westech, TES Slovakia, TSS Group, Asbis, UPC Broadband, Va-Mont Finance, Telekom, and O2 Slovakia invoices. Please check the PDF file.'))
        
        # Check if this is a credit note (dobropis/opravný doklad) - check both filename and text
        filename_lower = self.filename.lower() if self.filename else ''
        # Look for stronger indicators: document title line starting with these words, not just appearing anywhere
        is_refund = (
            'dobropis' in filename_lower or 
            re.search(r'^(?:dobropis|opravný\s+daňový\s+doklad)', text.lower(), re.MULTILINE) or
            'dôvod storna' in text.lower()
        )
        
        data = {
            'lines': [],
            'is_alza': is_alza,
            'is_westech': is_westech,
            'is_tes': is_tes,
            'is_refund': is_refund,
            'is_tss': is_tss,
            'is_asbis': is_asbis,
            'is_upc': is_upc,
            'is_vamont': is_vamont,
            'is_telekom': is_telekom,
            'is_o2': is_o2, 
        }
        if is_alza:
            data.update({ 'supplier_id': 21,})
        elif is_westech:
            data.update({ 'supplier_id': 1583,})
        elif is_tes:
            data.update({ 'supplier_id': 1649,})
        elif is_tss:
            data.update({ 'supplier_id': 1661,})
        elif is_asbis:
            data.update({ 'supplier_id': 19,})
        elif is_upc:
            data.update({ 'supplier_id': 1648,})
        elif is_vamont:
            data.update({ 'supplier_id': 1179,})
        elif is_o2:
            data.update({ 'supplier_id': 1653,})
        elif is_telekom:
            data.update({ 'supplier_id': 1662,})
        # Extract invoice number (common patterns)
        invoice_patterns = [
            r'Faktúra\s+(\d+)',  # Va-Mont format: "Faktúra 12500024"
            r'Poradové\s+číslo\s+faktúry\s*:\s*(\d+)',  # UPC format: "Poradové číslo faktúry: 214095500"
            r'Faktúra\s*-\s*daňový\s*doklad\s*č\.\s*:\s*(?:.*?)([A-Z]{2}-\d+/\d+)',  # TSS format: "Faktúra - daňový doklad č.: ... FV-3336/2025"
            r'Opravný\s+daňový\s+doklad\s*-\s*(\d+)',  # Alza credit note / corrective invoice number
            r'FAKTÚRA\s+číslo\s+(\d+)',  # TES format: "FAKTÚRA číslo 2512298"
            r'FAKTÚRA\s+(\d+)',  # WESTech format: "FAKTÚRA 1102526327"
            r'Faktúra\s*-\s*daňový\s*doklad\s*-\s*(\d+)',  # Alza invoice format
            r'Invoice\s*#?\s*:?:?\s*(\S+)',
            r'Faktura\s*č\.\s*:?:?\s*(\S+)',
            r'Invoice\s*Number\s*:?:?\s*(\S+)',
            r'Číslo\s*faktúry\s*:?:?\s*(\S+)',
        ]
        for pattern in invoice_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                data['invoice_number'] = match.group(1)
                break
        
        # Extract dates
        if data.get('is_tss'):
            # TSS-specific date extraction: "Dátum vystavenia dokladu: 22.12.2025"
            tss_date_match = re.search(r'Dátum vystavenia dokladu\s*:\s*(\d{1,2}\.\d{1,2}\.\d{4})', text)
            if tss_date_match:
                data['invoice_date'] = self._parse_date(tss_date_match.group(1))
            
            # TSS due date: "Dátum splatnosti: 05.01.2026"
            due_date_match = re.search(r'Dátum splatnosti\s*:\s*(\d{1,2}\.\d{1,2}\.\d{4})', text)
            if due_date_match:
                data['invoice_due_date'] = self._parse_date(due_date_match.group(1))
        else:
            # Generic date extraction for other suppliers
            date_pattern = r'(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})'
            dates = re.findall(date_pattern, text)
            if dates:
                data['invoice_date'] = self._parse_date(dates[0])
                if len(dates) > 1:
                    data['invoice_due_date'] = self._parse_date(dates[1])
        
        # Extract VAT/IČO (Slovak format)
        vat_patterns = [
            r'IČO\s*:?\s*(\d+)',
            r'VAT\s*:?\s*([A-Z]{2}\d+)',
            r'DIČ\s*:?\s*(\d+)',
        ]
        for pattern in vat_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                data['supplier_vat'] = match.group(1)
                break
        
        # Extract supplier name (line before IČO or at top)
        name_match = re.search(r'^(.+?)(?:\nIČO|$)', text, re.MULTILINE)
        if name_match:
            data['supplier_name'] = name_match.group(1).strip()
        
        # Extract amounts from tax breakdown section
        # Look for "Vyčíslenie DPH v EUR:" (Alza) or "DPH% Základ DPH(zaokr.)" (WESTech) section
        # Alza pattern: "23 % 15,65 3,60" (comma decimals)
        # WESTech pattern: "23% 286.18 65.82 352.00" (dot decimals)
        # We need to sum all base amounts and tax amounts
        
        total_untaxed = 0.0
        total_tax = 0.0
        
        # Find the tax breakdown section - support both comma and dot decimals, including negative values
        # Pattern supports: "23% 715.09 164.47" or "23% -715.09 -164.47"
        vat_breakdown_pattern = r'(\d+)\s*%\s+(-?[\d\s]+[,\.]\d+)\s+(-?[\d\s]+[,\.]\d+)'
        vat_matches = re.findall(vat_breakdown_pattern, text)
        
        for match in vat_matches:
            vat_rate = match[0]
            base_amount = match[1].replace(' ', '').replace(',', '.')
            tax_amount = match[2].replace(' ', '').replace(',', '.')
            
            try:
                total_untaxed += float(base_amount)
                total_tax += float(tax_amount)
            except ValueError:
                pass
        
        data['total_untaxed'] = total_untaxed
        data['total_tax'] = total_tax
        
        # Extract total amount (should match untaxed + tax)
        amount_patterns = [
            r'Sumy v EUR.*?Celkom\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)',  # TSS format: "Celkom 104,28 23,98 128,26"
            r'Celková hodnota faktúry\s*:?\s*(-?[\d\s,]+\.?\d*)',  # TES format (supports negative)
            r'Celkom k úhrade\s*:?\s*€?\s*(-?[\d\s,]+\.?\d*)',  # WESTech format
            r'Celkom\s*:?\s*€?\s*(-?[\d\s,]+\.?\d*)\s*EUR',
            r'Total\s*:?\s*€?\s*(-?[\d\s,]+\.?\d*)',
            r'Amount\s*:?\s*€?\s*(-?[\d\s,]+\.?\d*)',
        ]
        
        # Try TSS format first (has 3 values: untaxed, tax, total)
        tss_match = re.search(amount_patterns[0], text, re.IGNORECASE | re.DOTALL)
        if tss_match:
            try:
                untaxed_str = tss_match.group(1).replace(' ', '').replace(',', '.')
                tax_str = tss_match.group(2).replace(' ', '').replace(',', '.')
                total_str = tss_match.group(3).replace(' ', '').replace(',', '.')
                
                data['total_untaxed'] = float(untaxed_str)
                data['total_tax'] = float(tax_str)
                data['total_amount'] = float(total_str)
            except (ValueError, IndexError):
                pass
        
        # Try other patterns if TSS pattern didn't work
        if 'total_amount' not in data:
            for pattern in amount_patterns[1:]:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    amount_str = match.group(1).replace(' ', '').replace(',', '.')
                    try:
                        data['total_amount'] = float(amount_str)
                    except ValueError:
                        pass
                    break
        
        # If total_amount not found, calculate it
        if 'total_amount' not in data and total_untaxed > 0:
            data['total_amount'] = total_untaxed + total_tax
        
        # Try to extract table data using pdfplumber for better accuracy
        if pdfplumber and not data.get('is_tss') and not data.get('is_o2') and not data.get('is_telekom'):
            try:
                import io
                pdf_file = io.BytesIO(pdf_data)
                with pdfplumber.open(pdf_file) as pdf:
                    for page in pdf.pages:
                        tables = page.extract_tables()
                        for table in tables:
                            lines = self._parse_table_lines(table)
                            data['lines'].extend(lines)
            except Exception as e:
                _logger.warning("Table extraction failed: %s", str(e))
        
        # Fallback: parse lines from text if table extraction didn't work or gave bad results
        if data.get('is_tss'):
            data['lines'] = self._parse_tss_lines_from_text(text)
        elif data.get('is_asbis'):
            data['lines'] = self._parse_asbis_lines_from_text(text)
        elif data.get('is_upc'):
            data['lines'] = self._parse_upc_lines_from_text(text)
        elif data.get('is_vamont'):
            data['lines'] = self._parse_vamont_lines_from_text(text)
        elif data.get('is_o2'):
            data['lines'] = self._parse_o2_lines_from_text(text)
        elif data.get('is_telekom'):
            data['lines'] = self._parse_telekom_lines_from_text(text)
        elif not data['lines'] or len(data['lines']) > 20:  # Too many lines usually means bad parsing
            if data.get('is_westech'):
                data['lines'] = self._parse_westech_lines_from_text(text)
            elif data.get('is_tes'):
                data['lines'] = self._parse_tes_lines_from_text(text, is_refund=data.get('is_refund'))
                # Extract TES-specific VAT summary
                vat_header_start = text.lower().find('rozpis dph')
                if vat_header_start != -1:
                    vat_section = text[vat_header_start:vat_header_start + 500]
                    vat_breakdown_pattern = r'(\d+)\s*%\s+(-?[\d\s]+[,\.]\d+)\s+(-?[\d\s]+[,\.]\d+)'
                    vat_matches = re.findall(vat_breakdown_pattern, vat_section)
                    
                    total_untaxed = 0.0
                    total_tax = 0.0
                    for match in vat_matches:
                        base_amount = match[1].replace(' ', '').replace(',', '.')
                        tax_amount = match[2].replace(' ', '').replace(',', '.')
                        try:
                            total_untaxed += float(base_amount)
                            total_tax += float(tax_amount)
                        except ValueError:
                            pass
                    
                    if total_untaxed != 0:
                        data['total_untaxed'] = total_untaxed
                        data['total_tax'] = total_tax
            else:
                data['lines'] = self._parse_lines_from_text(text)
        
        return data

    def _parse_table_lines(self, table):
        """Parse invoice lines from extracted table"""
        lines = []
        
        if not table or len(table) < 2:
            return lines
        
        # Try to find header row to identify columns
        header_row = None
        header_indices = {}
        
        for idx, row in enumerate(table):
            if row and any(h in str(cell).lower() if cell else '' for cell in row for h in ['popis', 'ks', 'cena', 'description', 'qty', 'price', 'kod', 'kód']):
                header_row = idx
                # Map column names to indices
                for col_idx, cell in enumerate(row):
                    if cell:
                        cell_lower = str(cell).lower().strip()
                        if 'popis' in cell_lower or 'description' in cell_lower:
                            header_indices['description'] = col_idx
                        elif cell_lower in ['ks', 'qty', 'quantity']:
                            header_indices['quantity'] = col_idx
                        elif 'cena' in cell_lower and 'ks' in cell_lower:
                            header_indices['price'] = col_idx
                        elif 'kod' in cell_lower or 'kód' in cell_lower:
                            header_indices['code'] = col_idx
                break
        
        # Parse data rows
        start_row = (header_row + 1) if header_row is not None else 1
        
        for row in table[start_row:]:
            if not row or len(row) < 2:
                continue
            
            # Skip rows that look like totals or summaries
            row_text = ' '.join(str(cell) for cell in row if cell).lower()
            if any(keyword in row_text for keyword in ['celkom', 'total', 'spolu', 'suma', 'zaokrúhlenie', 'vyčíslenie', 'základ']):
                continue
            
            line = {}
            
            # Use header indices if available
            if header_indices:
                try:
                    # Get description
                    desc_idx = header_indices.get('description', 1)
                    code_idx = header_indices.get('code', 0)
                    
                    if desc_idx < len(row) and row[desc_idx]:
                        description = str(row[desc_idx]).strip()
                        # Add code if available
                        if code_idx < len(row) and row[code_idx]:
                            code = str(row[code_idx]).strip()
                            if code and code not in description:
                                description = f"{code} - {description}"
                        line['description'] = description
                    
                    # Get quantity
                    qty_idx = header_indices.get('quantity', 2)
                    if qty_idx < len(row) and row[qty_idx]:
                        try:
                            qty_str = str(row[qty_idx]).strip().replace(',', '.')
                            line['quantity'] = float(qty_str)
                        except (ValueError, AttributeError):
                            line['quantity'] = 1.0
                    
                    # Get price
                    price_idx = header_indices.get('price', 3)
                    if price_idx < len(row) and row[price_idx]:
                        try:
                            price_str = str(row[price_idx]).strip().replace(',', '.')
                            line['price_unit'] = float(price_str)
                        except (ValueError, AttributeError):
                            pass
                    
                except (IndexError, ValueError) as e:
                    _logger.warning("Error parsing row with headers: %s", e)
                    continue
            else:
                # Fallback: try to parse without header info
                for i, cell in enumerate(row):
                    if cell and isinstance(cell, str):
                        cell = cell.strip()
                        
                        # Check if it's a number
                        if re.match(r'^\d+[.,]?\d*$', cell):
                            num_value = float(cell.replace(',', '.'))
                            if 'quantity' not in line:
                                line['quantity'] = num_value
                            elif 'price_unit' not in line:
                                line['price_unit'] = num_value
                        elif cell and not line.get('description'):
                            line['description'] = cell
            
            # Only add line if we have at least a description
            if line.get('description'):
                # Set defaults
                if 'quantity' not in line:
                    line['quantity'] = 1.0
                if 'price_unit' not in line:
                    line['price_unit'] = 0.0
                lines.append(line)
        
        return lines

    def _parse_lines_from_text(self, text):
        """
        Alza invoice parser - handles products where code, description, and numbers are on same line.
        Format: CODE Description Qty Price Subtotal Tax TaxPct TotalPrice Warranty
        
        New logic: A line with pattern "Qty Price Price..." marks a product.
        All lines WITHOUT this pattern are description continuations.
        """
        rows = [r.strip() for r in text.split("\n")]
        items = []
        i = 0
        in_items = False
        
        # For Alza invoices: Look for VAT rate (0,5,10,15,17,18,19,20,23,25) and work backwards
        # This is robust because prices can have spaces and split into multiple tokens
        # Pattern (backwards from VAT rate): ... Price_with_spaces VAT_amount VAT_rate ...
        
        CODE_RE = re.compile(r'^[A-Za-z0-9]{3,}$')  # 3+ alphanumerics
        PRICE_RE = re.compile(r'^-?\d+[,\.]\d+')    # Decimal with comma/dot (may be negative)
        QTY_RE = re.compile(r'^\d{1,3}$')           # 1-3 digits only
        VAT_RATE_RE = re.compile(r'^(0|5|10|15|17|18|19|20|23|25)$')  # Valid VAT rates
        
        while i < len(rows):
            line = rows[i]

            # Start when we reach header
            if not in_items:
                if "Kód" in line and "Popis" in line:
                    in_items = True
                i += 1
                continue

            # Stop at totals
            if any(stop in line for stop in ["Celkom:", "Vyčíslenie", "Nehraďte"]):
                break

            if not line:
                i += 1
                continue

            # Token-based approach: Find VAT rate and work backwards
            # This handles prices with spaces that split into multiple tokens
            tokens = line.split()
            if not tokens:
                i += 1
                continue
            
            # Find product data by searching for VAT rate (and checking it's preceded by prices)
            product_data_start_idx = None
            vat_idx = None
            
            for idx in range(len(tokens)):
                if VAT_RATE_RE.match(tokens[idx]):
                    # Check that this is really a VAT rate (preceded by price-like token)
                    if idx > 0 and PRICE_RE.match(tokens[idx - 1]):
                        # This looks like a real VAT rate in a product line
                        vat_idx = idx
                        
                        # Now search backwards for Qty (1-3 digits)
                        # Skip over prices and other numeric data
                        for search_idx in range(idx - 1, -1, -1):
                            if QTY_RE.match(tokens[search_idx]):
                                # Found a potential qty - check if next token is price-like
                                if search_idx + 1 < len(tokens):
                                    next_token = tokens[search_idx + 1]
                                    # Next should be a price (with comma) or a single digit (part of multi-token price)
                                    if PRICE_RE.match(next_token) or next_token.isdigit():
                                        product_data_start_idx = search_idx
                                        break
                        
                        if product_data_start_idx is not None:
                            break
            
            if product_data_start_idx is None:
                i += 1
                continue
            
            # Extract description (everything before product data)
            code = None
            desc_tokens = tokens[:product_data_start_idx]
            
            if desc_tokens and CODE_RE.match(desc_tokens[0]):
                code = desc_tokens[0]
                desc_tokens = desc_tokens[1:]
            
            desc_part = ' '.join(desc_tokens)
            
            # Collect continuation lines (lines without product data pattern)
            j = i + 1
            continuation_lines = []
            while j < len(rows):
                next_line = rows[j].strip()
                if not next_line:
                    break
                
                # Stop at totals
                if any(stop in next_line for stop in ["Celkom:", "Vyčíslenie", "Nehraďte"]):
                    break
                
                # If next line has product data pattern, it's a new product
                next_tokens = next_line.split()
                is_next_product = False
                for idx in range(len(next_tokens)):
                    if VAT_RATE_RE.match(next_tokens[idx]) and idx > 0 and PRICE_RE.match(next_tokens[idx - 1]):
                        is_next_product = True
                        break
                
                if is_next_product:
                    break
                
                # This is a continuation line - add to description
                continuation_lines.append(next_line)
                
                i = j
                j += 1
            
            # Build full description
            full_description = desc_part
            if continuation_lines:
                full_description += ' ' + ' '.join(continuation_lines)
            
            # Skip free items (ZADARMO) regardless of product type
            if "zadarmo sim karta" in full_description.lower():
                _logger.info(f"Skipping free item: {full_description[:50]}")
                i += 1
                continue
            
            # Skip only shipping and discount intangible products
            if "Nehmotný produkt" in full_description and any(skip in full_description.lower() for skip in ["doprava", "na doručenie"]):
                _logger.info(f"Skipping intangible product: {full_description[:50]}")
                i += 1
                continue
            
            # Extract quantity and price from the tokens
            qty = float(tokens[product_data_start_idx])
            
            # Price extraction needs to handle multi-token prices (with spaces as thousands separators)
            # Examples: "1 088,62" becomes ["1", "088,62"] or "250,38" stays as one token
            # Key insight: If qty is 1-3 digits and next token is "XXX,YY" (starts with leading 0 or is 3 digits),
            # it's likely part of a multi-token price like "1 088,62"
            price_token = tokens[product_data_start_idx + 1]
            
            # Check if this is a partial price token (starts with "0" suggesting it's thousands group)
            is_partial_price = (
                price_token and 
                re.match(r'^0\d*,', price_token) and  # Starts with "0" before decimal (e.g., "088,62")
                product_data_start_idx + 2 < len(tokens)
            )
            
            if is_partial_price:
                # Combine with previous token (which is qty but forms the beginning of price)
                # This is a bit of a hack, but we need to treat qty+next as forming the price
                # Since qty was "1" and next is "088,62", together they make "1088,62"
                price_str = str(int(qty)) + tokens[product_data_start_idx + 1]
            elif price_token.isdigit():
                # This is just the thousands part (like "1" from "1 088,62")
                # Next token should have the decimal
                if product_data_start_idx + 2 < len(tokens):
                    price_str = price_token + tokens[product_data_start_idx + 2]
                else:
                    price_str = price_token
            else:
                # This token has a decimal (comma or dot) and doesn't start with 0
                price_str = price_token
            
            price = float(price_str.replace(' ', '').replace(',', '.'))
            # VAT rate is already found and validated
            vat_rate = float(tokens[vat_idx])
            
            items.append({
                "description": full_description.strip(),
                "quantity": qty,
                "price_unit": price,
                "vat_rate": vat_rate,
            })

            i += 1

        return items

    def _parse_westech_lines_from_text(self, text):
        """
        WESTech invoice parser - handles products with format:
        Kód Názov produktu PočetHmotnosťkg Cena/MJ RP L/S Cena DPH % Celkom
        Example: NTWUB-U6-IW Ubiquiti UniFi AP 6 InWall
                 24 mesiacov 2 1.216 142.962 0.13 0.00 286.18 23% 352.01
        """
        rows = [r.strip() for r in text.split("\n")]
        items = []
        i = 0
        in_items = False
        
        # Pattern to identify product code (alphanumeric, typically starts with letters)
        CODE_RE = re.compile(r'^[A-Z0-9]{3,}')
        # Pattern for numeric data line - look for the distinctive DPH% pattern and work from there
        # Example: "12 mesiacov 1 2.428 849.940 0.42 6.05 856.41 23% 1 053.38"
        # Example: "12 mesiacov 1 3.018 1 060.200 0.42 6.05 1 066.67 23% 1 312.00"
        # The line format is: [warranty info] Počet Hmotnosť Cena/MJ RP L/S Cena DPH% Celkom
        # Numbers with spaces (like "1 060.200") need special handling
        # Use \b for word boundaries and allow optional spaces within numbers for thousands
        DATA_PATTERN = re.compile(r'(\d+)\s+([\d.]+)\s+((?:\d+\s)*\d+\.[\d]+)\s+([\d.]+)\s+([\d.]+)\s+((?:\d+\s)*\d+\.[\d]+)\s+(\d+)%')

        while i < len(rows):
            line = rows[i]

            # Start when we reach header
            if not in_items:
                if "Kód" in line and "Názov produktu" in line:
                    in_items = True
                i += 1
                continue

            # Stop at totals or summary sections
            if any(stop in line for stop in ["Z celkovej sumy", "DPH%", "Základ", "Prevzal"]):
                break

            if not line:
                i += 1
                continue

            # Check if line starts with product code
            tokens = line.split()
            if not tokens:
                i += 1
                continue
            
            first = tokens[0]
            
            if CODE_RE.match(first):
                # This is a product line
                # Collect description (current line and next lines until we hit data)
                description_parts = [' '.join(tokens[1:])]  # Rest of first line after code
                
                j = i + 1
                while j < len(rows):
                    next_line = rows[j].strip()
                    if not next_line:
                        break
                    
                    # Check if this line has the data pattern
                    if DATA_PATTERN.search(next_line):
                        # Parse the data
                        data_match = DATA_PATTERN.search(next_line)
                        if data_match:
                            # Extract and clean the values
                            qty_str = data_match.group(1).strip()
                            weight_str = data_match.group(2).strip()
                            price_str = data_match.group(3).strip()  # May contain spaces like "1 060.200"
                            rp_str = data_match.group(4).strip()
                            ls_str = data_match.group(5).strip()
                            cena_str = data_match.group(6).strip()
                            vat_str = data_match.group(7).strip()
                            
                            # Remove spaces from numbers (thousands separators)
                            qty = float(qty_str)
                            price = float(cena_str.replace(' ', ''))  # Group 6 is "Cena/MJ" (price per unit without DPH)
                            vat_rate = float(vat_str)  # Group 7 is DPH%
                            
                            _logger.info(f"Parsed: qty={qty}, price={price}, vat={vat_rate}% from line: {next_line}")
                            
                            # Before the data pattern might be warranty info - skip it
                            # (e.g., "24 mesiacov" before the numeric data)
                            
                            full_description = ' '.join(description_parts).strip()
                            
                            items.append({
                                "description": full_description,
                                "quantity": qty,
                                "price_unit": price / qty if qty != 0 else price,
                                "vat_rate": vat_rate,
                            })
                        
                        i = j
                        break
                    else:
                        # This is part of description
                        # Check if this line starts with a new product code - if so, stop
                        next_tokens = next_line.split()
                        if next_tokens and CODE_RE.match(next_tokens[0]):
                            # This is a new product, don't include it in description
                            break
                        
                        # Skip serial numbers, warranty info, and other metadata
                        if not any(skip in next_line for skip in ["mesiacov", "Sériové", ";"]):
                            description_parts.append(next_line)
                    
                    j += 1
                    i = j - 1

            i += 1

        # Extract recycling fee (Recyklačný poplatok) if present
        # Pattern: "Recyklačný poplatok (DPH 23%): 0.84 EUR"
        # recycling_pattern = r'Recyklačný poplatok\s*\(DPH\s*(\d+)%\)\s*:\s*([\d.]+)\s*EUR'
        # recycling_match = re.search(recycling_pattern, text, re.IGNORECASE)
        
        # if recycling_match:
        #     vat_rate = float(recycling_match.group(1))
        #     total_amount = float(recycling_match.group(2))
        #     price_unit = total_amount
            
        #     items.append({
        #         "description": f"Recyklačný poplatok (DPH {int(vat_rate)}%)",
        #         "quantity": 1,
        #         "price_unit": round(price_unit, 2),
        #         "vat_rate": vat_rate,
        #     })
        
        # # Extract SOZA fee (SOZA poplatok) if present
        # # Pattern: "SOZA poplatok (DPH 23%): 12.10 EUR"
        # soza_pattern = r'SOZA poplatok\s*\(DPH\s*(\d+)%\)\s*:\s*([\d.]+)\s*EUR'
        # soza_match = re.search(soza_pattern, text, re.IGNORECASE)
        
        # if soza_match:
        #     vat_rate = float(soza_match.group(1))
        #     total_amount = float(soza_match.group(2))
        #     price_unit = total_amount
            
        #     items.append({
        #         "description": f"SOZA poplatok (DPH {int(vat_rate)}%)",
        #         "quantity": 1,
        #         "price_unit": round(price_unit, 2),
        #         "vat_rate": vat_rate,
        #     })

        return items

    def _parse_tes_lines_from_text(self, text, is_refund=False):
        """
        TES-Slovakia invoice parser - handles products with format:
        Kód Názov produktu Počet MJ Cena/MJ DPH% Základ DPH Celkom
        Example: S04072 Ubiquiti 10G SFP+ DAC kábel, pasívny, DDM, 1m 5 ks 11.2194 23% 56.10 12.90 69.00
        """
        rows = [r.strip() for r in text.split("\n")]
        items = []
        i = 0
        in_items = False
        
        # Pattern to identify product code (starts with letter/number, followed by digits)
        CODE_RE = re.compile(r'^[A-Z]\d{5}')  # TES codes like S04072
        # Pattern for numeric data line - Počet MJ Cena/MJ DPH% Základ DPH Celkom
        # Handles both positive and negative quantities and amounts
        # Example: "5 ks 11.2194 23% 56.10 12.90 69.00" or "-1 bal. 715.0000 23% -715.00 -164.45 -879.45"
        # Also handles units like: ks, bal., kus, ks., m, km, kg, l, dní (days), hod. (hours), etc.
        DATA_PATTERN = re.compile(r'(-?\d+)\s+(?:ks|bal\.|kus|ks\.|m|km|kg|l|dní|hod\.|hod|deň|dni)\s+([\d.]+)\s+(\d+)%\s+([-\d.\s]+?)\s+([-\d.\s]+?)\s+([-\d.\s]+?)(?:\s|$)')

        while i < len(rows):
            line = rows[i]

            # Start when we reach header
            if not in_items:
                if "Kód" in line and "Názov produktu" in line:
                    in_items = True
                i += 1
                continue

            # Stop at totals or summary sections (but continue to parse RECFEE lines)
            if any(stop in line for stop in ["Celková hodnota", "Už zaplatené", "Celkom k úhrade", "Rozpis DPH"]):
                # But don't break if this is a RECFEE line
                if "RECFEE" not in line:
                    break

            if not line:
                i += 1
                continue

            # Check if this line contains product data
            data_match = DATA_PATTERN.search(line)
            
            if data_match:
                # This line has product data - it's a product line
                # Everything before the numbers is the description
                desc_part = line[:data_match.start()].strip()
                
                # Check if description contains a product code in the middle/end
                # This indicates that a previous product code got concatenated
                # Pattern: look for product code not at the beginning
                code_matches = list(CODE_RE.finditer(desc_part))
                if len(code_matches) > 1:
                    # Multiple product codes found - this is a merge of two lines
                    # Find the last product code and everything from there is the next product
                    last_code_match = code_matches[-1]
                    # Extract only up to the last product code
                    desc_part = desc_part[:last_code_match.start()].strip()
                elif len(code_matches) == 1 and code_matches[0].start() > 0:
                    # Single product code but not at start - likely from concatenation
                    # Check if there's meaningful text before it
                    before_code = desc_part[:code_matches[0].start()].strip()
                    if before_code and ' ' in before_code:
                        # There's text before the code, likely a different product
                        desc_part = before_code
                
                # Collect continuation lines (lines without product data pattern)
                j = i + 1
                continuation_lines = []
                while j < len(rows):
                    next_line = rows[j].strip()
                    if not next_line:
                        break
                    
                    # Stop at totals (but not on RECFEE lines)
                    if any(stop in next_line for stop in ["Celková hodnota", "Už zaplatené", "Celkom k úhrade", "Rozpis DPH"]):
                        if "RECFEE" not in next_line:
                            break
                    
                    # If next line has product data pattern, it's a new product
                    if DATA_PATTERN.search(next_line):
                        break
                    
                    # If next line starts with a product code, it's a new product
                    next_tokens = next_line.split()
                    if next_tokens and CODE_RE.match(next_tokens[0]):
                        break
                    
                    # This is a continuation line - add to description
                    if next_line and "(RECFEE)" not in next_line:
                        continuation_lines.append(next_line)
                    
                    i = j
                    j += 1
                
                # Build full description
                full_description = desc_part
                if continuation_lines:
                    full_description += ' ' + ' '.join(continuation_lines)
                
                # Remove product code from start if present
                tokens = full_description.split()
                if tokens and CODE_RE.match(tokens[0]):
                    full_description = ' '.join(tokens[1:])

                
                # Extract quantity and price from the match
                qty = float(data_match.group(1))
                price_str = data_match.group(2).replace(' ', '')  # Remove spaces from number
                price = float(price_str)
                vat_rate = int(data_match.group(3))
                
                # Debug logging
                _logger.info(f"TES Parser - Line {i}: desc='{full_description}' qty={qty} price={price} vat={vat_rate}%")
                
                # For refunds, ensure price is negative; for regular invoices, ensure it's positive
                if is_refund:
                    if price > 0:
                        price = -price
                else:
                    if price < 0:
                        price = -price
                
                # Only add if we have a valid description
                if full_description and full_description.strip():
                    items.append({
                        "description": full_description.strip(),
                        "quantity": abs(qty),
                        "price_unit": price,
                        "vat_rate": vat_rate,
                    })

            i += 1

        return items

    def _parse_tss_lines_from_text(self, text):
        """
        TSS Group invoice parser

        Rule:
        - A NEW item exists ONLY if the line contains the FULL numeric signature:
        x,xxxks  price  discount%  unit_after_discount  subtotal  VAT%  total_with_vat
        - Any other line = description continuation of the previous item
        - Recycling fee lines are ignored
        """

        rows = [r.strip() for r in text.split("\n")]
        items = []

        # STRICT numeric signature of a real product line
        # Handles numbers with spaces as thousands separators (e.g., 2 440,00)
        # Supports units: ks, set, bal., kus, etc.
        # Looks for: qty + unit + [anything] + LAST_vat% + total
        # Uses greedy matching (.*) to consume up to the last %
        # PRICE_PATTERN = re.compile(
        #     r'(?P<qty>\d+),\d+(?:ks|set|bal\.|kus|bal|piece|pieces)\s+'
        #     r'.*?'  # Greedy match everything (prices, discounts, etc)
        #     r'(?P<vat>\d+)%\s+'
        #     r'(?P<total>[\d,\s]+?)(?:\s|$)'
        # )
        TSS_LINE_PATTERN = re.compile(
    r'(?P<qty>\d+),\d+(?P<unit>ks|set|bal\.|kus|bal)\s+'
    r'(?P<orig_price>[\d,]+)\s+'
    r'(?P<discount>\d+)%\s+'
    r'(?P<unit_after_discount>[\d,]+)\s+'
    r'(?P<subtotal>[\d,]+)\s+'
    r'(?P<vat>\d+)%\s+'
    r'(?P<total>[\d,]+)'
)


        current_item = None

        for row in rows:
            if not row:
                continue

            # stop ONLY after at least one product was parsed
            if current_item and any(x in row for x in [
                'Dodacie listy',
                'Objednávky',
                'Sumy v EUR',
                'Celková zľava',
                'Celková cena recyklačného',
                'Vystavil',
            ]):
                break


            # ignore recycling fee
            if 'recyklačný poplatok' in row.lower():
                continue
            # transport / shipping cost
            if any(x in row.lower() for x in [
                'náklady spojené s prepravou',
                'transport',
                'doprava',
            ]):
                m = re.search(r'(\d+),\d+ks\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+(\d+)%\s+([\d,]+)', row)
                if m:
                    qty = int(m.group(1))
                    price_unit = float(m.group(2).replace(',', '.'))
                    vat_rate = int(m.group(5))

                    items.append({
                        "description": "Náklady spojené s prepravou",
                        "quantity": qty,
                        "price_unit": price_unit,
                        "vat_rate": vat_rate,
                    })
                continue


            match = TSS_LINE_PATTERN.search(row)

            if match:
                # flush previous item
                if current_item:
                    items.append(current_item)

                quantity = int(match.group('qty'))
                price_unit = float(match.group('unit_after_discount').replace(',', '.'))
                vat_rate = int(match.group('vat'))

                # Get description - everything before the numeric pattern
                description = row[:match.start()].strip()
                
                # Check if there are multiple product codes in the description
                # Pattern for product codes: letters followed by digits/hyphens
                code_pattern = re.compile(r'([A-Z][A-Z0-9-]{4,})')
                codes = list(code_pattern.finditer(description))
                
                if len(codes) > 1:
                    # Multiple product codes found - keep only the last one and its description
                    last_code_pos = codes[-1].start()
                    description = description[last_code_pos:]
                
                # Remove warranty/guarantee info and anything after it
                # Stop at "Záruka:", "Garancia:", "Warranty:" etc.
                warranty_match = re.search(r'\s*(?:Záruka|Garancia|Warranty):', description, re.IGNORECASE)
                if warranty_match:
                    description = description[:warranty_match.start()].strip()

                current_item = {
                    "description": description,
                    "quantity": quantity,
                    "price_unit": price_unit,
                    "vat_rate": vat_rate,
                }
            else:
                # Check if this line starts with a product code (e.g., NVR5416-XI)
                # Product codes are typically: letters followed by digits/hyphens
                tokens = row.split()
                starts_with_code = (
                    tokens and 
                    re.match(r'^[A-Z][A-Z0-9-]{4,}', tokens[0])  # Code like NVR5416-XI or PFB205W-E
                )
                
                if starts_with_code and current_item:
                    # This is a new product starting, but split across multiple lines
                    # The numeric data (qty/price) is on this new line, just not parsed yet
                    # Add to current_item's description for now
                    current_item["description"] += " " + row
                elif current_item:
                    # description continuation
                    current_item["description"] += " " + row

        # flush last item
        if current_item:
            items.append(current_item)

        return items



    def _parse_asbis_lines_from_text(self, text):
        """
        ASBIS invoice parser - handles products with format:
        Code Qty Unit Cena/MJ RP L/S/NOAZ Cena/MJ_s_popl DPH% Total (one line)
        Description (next line)
        
        Example:
        SKSSVERTEXPx-1200 1 ks 227.52 0.13 0.00 227.65 23% 280.01
        Zdroj 1200W, Seasonic VERTEX PX-1200 Platinum, retail
        
        Key: Extract Cena/MJ (second price group, right before VAT%)
        Pattern groups: (qty) (cena_mj) (rp) (l_s) (cena_mj_s_popl) (vat%) (total)
        """
        rows = [r.strip() for r in text.split("\n")]
        items = []
        i = 0
        
        # Pattern to identify the full product data line with VAT%
        # Code Qty Unit Cena/MJ RP L/S/NOAZ Cena/MJ_s_popl VAT% Total
        # Groups: qty, price_per_unit, rp, ls, price_with_fees, vat%, total
        PRODUCT_DATA_PATTERN = re.compile(
            r'(\d{1,3})\s+ks\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)%\s+([\d.]+)'
        )
        
        while i < len(rows):
            row = rows[i]
            
            # Skip empty lines and headers
            if not row or 'Kód' in row or 'Počet' in row or 'Názov produktu' in row:
                i += 1
                continue
            
            # Stop at totals section
            if any(x in row for x in ['Celková hodnota', 'Už zaplatené', 'Celkom k úhrade', 
                                        'Rozpis DPH', 'Z celkovej sumy', 'Poplatky celkom',
                                        'Vystavil:', 'QR kód']):
                break
            
            # Check if this row contains the product data line (has VAT% pattern)
            match = PRODUCT_DATA_PATTERN.search(row)
            if match:
                # Extract data from the match
                quantity = int(match.group(1))
                
                # Use Cena/MJ (second price group, right before VAT)
                price_unit = float(match.group(5))
                
                # VAT rate
                vat_rate = int(match.group(6))
                
                # Extract description - everything before the quantity pattern (the code)
                code_part = row[:match.start()].strip()
                
                # Get description from next line
                description = code_part  # Start with code part as fallback
                if i + 1 < len(rows):
                    next_line = rows[i + 1].strip()
                    # If next line doesn't contain product data pattern, it's a description
                    if next_line and not PRODUCT_DATA_PATTERN.search(next_line):
                        description = next_line
                        i += 1  # Skip the description line
                
                if description:
                    items.append({
                        "description": description,
                        "quantity": float(quantity),
                        "price_unit": round(price_unit, 2),
                        "vat_rate": vat_rate,
                    })
            
            i += 1
        
        return items

    def _parse_upc_lines_from_text(self, text):
        """
        UPC Broadband Slovakia invoice parser - handles products with format:
        Description Period BaseAmount TaxAmount TotalAmount
        Example: "Prístup do siete internet 11.12.2025 - 10.01.2026 24,44 5,63 30,07"
        """
        rows = [r.strip() for r in text.split("\n")]
        items = []
        vat_rate = 23  # Default VAT rate
        
        # Extract VAT rate from "Sadzba 23%" pattern
        for row in rows:
            vat_match = re.search(r'Sadzba\s+(\d+)%', row)
            if vat_match:
                vat_rate = int(vat_match.group(1))
                break
        
        in_items = False
        for row in rows:
            # Look for section start indicator
            if 'Pravidelné poplatky' in row or 'Obdobie' in row:
                in_items = True
                continue
            
            # Stop at summary sections
            if any(x in row for x in ['Sadzba', 'Spolu za DPH', 'Vyúčtovanie', 'Sumy bez DPH']):
                # But continue if we're still in items section and this is VAT rate line
                if 'Sadzba' in row:
                    continue
                else:
                    break
            
            if not in_items or not row:
                continue
            
            # Skip header rows and section labels
            if any(x in row for x in ['Suma bez DPH', 'DPH', 'Suma s DPH', 'Položka', 'Popis']):
                continue
            
            # Pattern to match: Description DateRange BaseAmount TaxAmount TotalAmount
            # The key is to find the date range pattern (dd.mm.yyyy - dd.mm.yyyy) and work from there
            # Example: "Prístup do siete internet 11.12.2025 - 10.01.2026 24,44 5,63 30,07"
            # Group 1: Description (everything before date)
            # Group 2: Start date
            # Group 3: End date  
            # Group 4: Base amount
            # Group 5: Tax amount
            # Group 6: Total amount
            date_range_pattern = r'^(.+?)\s+(\d{1,2}\.\d{1,2}\.\d{4})\s*-\s*(\d{1,2}\.\d{1,2}\.\d{4})\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$'
            match = re.search(date_range_pattern, row)
            
            if match:
                try:
                    description = match.group(1).strip()
                    # Skip rows that are clearly not items
                    if any(x in description for x in ['Součet', 'Celkem', 'Spolu', 'Suma']):
                        continue
                    
                    base_amount = float(match.group(4).replace(',', '.'))
                    # Tax amount is the second number
                    tax_amount = float(match.group(5).replace(',', '.'))
                    # Total amount (for validation)
                    total_amount = float(match.group(6).replace(',', '.'))
                    
                    # For monthly services, quantity is 1
                    # Price unit is the base amount (without tax)
                    quantity = 1.0
                    price_unit = base_amount
                    
                    items.append({
                        "description": description,
                        "quantity": quantity,
                        "price_unit": price_unit,
                        "vat_rate": vat_rate,
                    })
                except (ValueError, AttributeError):
                    pass
        
        return items

    def _parse_o2_lines_from_text(self, text):
        """
        O2 Slovakia invoice parser - extracts line items from VAT summary section.
        
        Since O2 invoices don't provide per-line VAT breakdown in the summary,
        we create one line per VAT rate using the base amounts (Základ dane)
        from the "Rekapitulácia DPH" section.
        
        Example:
        Rekapitulácia DPH
        Sadzba DPH | Základ dane | DPH | Celkom
        DPH 0%    | 99,69 €     | 0,00 € | 99,69 €
        DPH 23%   | 1.163,27 €  | 267,55 € | 1.430,82 €
        
        Creates:
        - "DPH 0%" with amount 99.69 and vat_rate 0
        - "DPH 23%" with amount 1163.27 and vat_rate 23
        """
        items = []
        
        # Find Rekapitulácia DPH section
        recap_start = text.lower().find('rekapitulácia dph')
        if recap_start == -1:
            _logger.warning("Could not find 'Rekapitulácia DPH' section in O2 invoice")
            return items
        
        # Get text from Rekapitulácia onwards
        recap_text = text[recap_start:]
        rows = [r.strip() for r in recap_text.split("\n")]
        
        # Pattern to match DPH lines in recap section
        # "DPH 0% 99,69 € 0,00 € 99,69 €"
        # "DPH 23% (U) Tuzem. 1.163,27 € 267,55 € 1.430,82 €"
        # Groups: (vat_rate) (base_amount)
        dph_pattern = r'DPH\s+(\d+)%.*?([\d\s\.]+,\d+)\s*€'
        
        seen = set()
        
        for row in rows:
            if not row or len(row) < 5:
                continue
            
            # Stop when we reach company info section (after VAT recap)
            if any(x in row for x in ['Slovakia, s.r.o.', 'E-mail:', 'Website:', 'IČO:']):
                break
            
            # Skip header row
            if 'Sadzba DPH' in row or 'Základ dane' in row:
                continue
            
            # Match DPH line
            match = re.search(dph_pattern, row)
            if match:
                vat_rate = int(match.group(1))
                base_amount_str = match.group(2).strip()
                
                # Create a line for this VAT rate
                desc = f"DPH {vat_rate}%"
                
                if desc not in seen:
                    try:
                        # Convert "1.163,27" to float
                        base_amount = float(base_amount_str.replace(' ', '').replace('.', '').replace(',', '.'))
                        if base_amount > 0:
                            items.append({
                                "description": desc,
                                "quantity": 1.0,
                                "price_unit": base_amount,
                                "vat_rate": vat_rate,
                            })
                            seen.add(desc)
                            _logger.info(f"O2 Parser: Extracted {desc} with amount {base_amount}€ and VAT {vat_rate}%")
                    except ValueError as e:
                        _logger.warning(f"Could not parse O2 base amount '{base_amount_str}': {e}")
        
        return items

    def _parse_telekom_lines_from_text(self, text):
        """
        Slovak Telekom invoice parser - extracts line items from charges summary.
        
        Telekom format has columns: Description | Base Amount | DPH Amount | Total
        "Poplatky s DPH 23 % 2 076,7642 † 477,66 † 2 554,42 †"  → Base: 2076.76, VAT: 23%
        "Poplatky, na ktoré sa neuplatňuje DPH 335,8700 † 0,00 † 335,87 †"  → Base: 335.87, VAT: 0%
        
        Processes only first page.
        """
        items = []
        rows = [r.strip() for r in text.split("\n")]
        
        # Pattern 1: "Poplatky s DPH X %" - extract FIRST amount after %
        # "Poplatky s DPH 23 % 2 076,7642 † 477,66 † 2 554,42 †"
        # After the %, the first amount is the base
        dph_pattern = r'Poplatky\s+s\s+DPH\s+(\d+)\s*%\s+([\d\s\.]+,\d+)'
        
        # Pattern 2: "Poplatky, na ktoré sa neuplatňuje DPH" - extract FIRST amount after description
        # "Poplatky, na ktoré sa neuplatňuje DPH označené * 335,8700 † 0,00 † 335,87 †"
        # The first amount after the text is the base
        no_dph_pattern = r'Poplatky,\s+na\s+ktoré\s+sa\s+neuplatňuje\s+DPH[^0-9]+([\d\s\.]+,\d+)'
        
        seen = set()
        
        for row in rows:
            if not row or len(row) < 5:
                continue
            
            # Stop at various end markers
            if any(x in row.lower() for x in ['banková', 'e-mail', 'website', 'kontakt', 'podpis', 'pečať', 'obchodný register']):
                break
            
            # Match "Poplatky s DPH X%" pattern
            match = re.search(dph_pattern, row)
            if match:
                vat_rate = int(match.group(1))
                base_amount_str = match.group(2).strip()
                
                desc = f"Poplatky s DPH {vat_rate}%"
                
                if desc not in seen:
                    try:
                        # Convert "2 076,7642" to 2076.7642
                        base_amount = float(base_amount_str.replace(' ', '').replace('.', '').replace(',', '.'))
                        if base_amount > 0:
                            items.append({
                                "description": desc,
                                "quantity": 1.0,
                                "price_unit": base_amount,
                                "vat_rate": vat_rate,
                            })
                            seen.add(desc)
                            _logger.info(f"Telekom Parser: Extracted {desc} with amount {base_amount}€ and VAT {vat_rate}%")
                    except ValueError as e:
                        _logger.warning(f"Could not parse Telekom amount '{base_amount_str}': {e}")
                continue
            
            # Match "Poplatky, na ktoré sa neuplatňuje DPH" pattern (0% VAT)
            match = re.search(no_dph_pattern, row)
            if match:
                desc = "Poplatky bez DPH"
                base_amount_str = match.group(1).strip()
                
                if desc not in seen:
                    try:
                        # Convert "335,8700" to 335.87
                        base_amount = float(base_amount_str.replace(' ', '').replace('.', '').replace(',', '.'))
                        if base_amount > 0:
                            items.append({
                                "description": desc,
                                "quantity": 1.0,
                                "price_unit": base_amount,
                                "vat_rate": 0,
                            })
                            seen.add(desc)
                            _logger.info(f"Telekom Parser: Extracted {desc} with amount {base_amount}€ and VAT 0%")
                    except ValueError as e:
                        _logger.warning(f"Could not parse Telekom no-DPH amount '{base_amount_str}': {e}")
        
        return items

    def _parse_vamont_lines_from_text(self, text):
        """
        Va-Mont Finance invoice parser - handles products with format:
        Description Quantity Unit Price Total
        Example: "Účtovníctvo 6/2025 1,00 200,00 200,00"
        """
        rows = [r.strip() for r in text.split("\n")]
        items = []
        
        # Check if this is a non-VAT payer invoice
        is_non_vat_payer = 'NIE SME PLATITELIA DPH' in text
        vat_rate = 0 if is_non_vat_payer else 20  # 0% for non-VAT payers, 20% otherwise
        
        # Extract VAT rate if available (fallback for VAT payers)
        if not is_non_vat_payer:
            vat_patterns = [
                r'(?:DPH|VAT|sadzba)\s+(\d+)\s*%',
                r'(\d+)\s*%\s+(?:DPH|VAT)',
            ]
            for row in rows:
                for pattern in vat_patterns:
                    vat_match = re.search(pattern, row, re.IGNORECASE)
                    if vat_match:
                        vat_rate = int(vat_match.group(1))
                        break
        
        in_items = False
        for row in rows:
            # Look for section start - the header line with "Označenie dodávky", "Počet", etc.
            if 'Označenie dodávky' in row or 'Katalóg. označenie' in row or 'Cena za m. j.' in row:
                in_items = True
                continue
            
            # Stop at summary/footer sections
            if any(x in row for x in ['Zaokrúhlenie', 'Zľava', 'Spolu na úhradu', 'Zaplatený preddavok', 
                                        'Zostáva uhradiť', 'Pečiatka', 'Spracované systémom', 'Vytlačil']):
                in_items = False
                continue
            
            if not in_items or not row:
                continue
            
            # Skip divider lines
            if row.startswith('.') or row.startswith('-') or len(row) < 5:
                continue
            
            # Pattern: Description followed by numbers (Qty, Unit Price, Total)
            # Format: "Description 1,00 200,00 200,00"
            # The key is: Description can have spaces, followed by 1-2 space-separated numbers
            # Groups: Description (1), Quantity (2), Price per unit (3), Total (4)
            pattern = r'^(.+?)\s+([\d,\.]+)\s+([\d,\.]+)\s+([\d,\.]+)\s*$'
            match = re.search(pattern, row)
            
            if match:
                try:
                    description = match.group(1).strip()
                    
                    # Skip if description is too short or is a known skip word
                    if len(description) < 2:
                        continue
                    
                    quantity = float(match.group(2).replace(',', '.'))
                    price_unit = float(match.group(3).replace(',', '.'))
                    # Total is match.group(4) - could be used for validation if needed
                    
                    items.append({
                        "description": description,
                        "quantity": quantity,
                        "price_unit": price_unit,
                        "vat_rate": vat_rate,
                    })
                except ValueError:
                    pass
        
        return items

    def _parse_date(self, date_str):


        """Parse date string to date object"""
        try:
            from datetime import datetime
            # Try different formats
            for fmt in ['%d.%m.%Y', '%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d']:
                try:
                    return datetime.strptime(date_str, fmt).date()
                except ValueError:
                    continue
        except Exception:
            pass
        return False

    def _find_or_create_supplier(self, vat, name=None):
        """Find supplier by VAT or create if not exists"""
        if not vat:
            return False
        
        # Search by VAT
        supplier = self.env['res.partner'].search([
            ('vat', 'ilike', vat),
            ('supplier_rank', '>', 0),
        ], limit=1)
        
        if not supplier and name:
            # Create new supplier
            supplier = self.env['res.partner'].create({
                'name': name,
                'vat': vat,
                'supplier_rank': 1,
                'company_type': 'company',
            })
        
        return supplier

    def _find_product(self, description, supplier_id=None):
        """Try to find existing product by name or pairing rule
        Returns tuple: (product_id, pack_qty)
        """
        if not description:
            return False, 1
        
        # First try pairing rules
        pairing_rule = self.env['product.pairing.rule'].search([
            ('name', '=ilike', description),
            ('active', '=', True),
        ], limit=1)
        
        if pairing_rule:
            # If we have a pairing rule with exact match, use it
            if supplier_id and pairing_rule.supplier_id.id == supplier_id:
                return pairing_rule.product_id.id, pairing_rule.pack_qty
            elif not supplier_id and not pairing_rule.supplier_id:
                return pairing_rule.product_id.id, pairing_rule.pack_qty
            
            # Try supplier-specific match
            supplier_rule = self.env['product.pairing.rule'].search([
                ('name', '=ilike', description),
                ('supplier_id', '=', supplier_id),
                ('active', '=', True),
            ], limit=1)
            if supplier_rule:
                return supplier_rule.product_id.id, supplier_rule.pack_qty
        
        # Fallback to direct product search (no pack qty)
        product = self.env['product.product'].search([
            ('name', 'ilike', description)
        ], limit=1)
        
        return (product.id if product else False), 1

    def action_create_invoice(self):
        """Create supplier invoice from extracted data"""
        self.ensure_one()
        
        if not self.supplier_id:
            raise UserError(_('Please select a supplier before creating the invoice.'))
        
        if not self.line_ids:
            raise UserError(_('No invoice lines found. Please process the PDF first.'))
        
        try:
            # Determine move type based on whether it's a refund
            move_type = 'in_refund' if self.is_refund else 'in_invoice'
            
            # Prepare invoice values
            invoice_vals = {
                'move_type': move_type,
                'partner_id': self.supplier_id.id,
                'invoice_date': self.invoice_date or fields.Date.today(),
                'invoice_date_due': self.invoice_due_date,
                'ref': self.invoice_number or self.name,
                'company_id': self.company_id.id,
                'currency_id': self.currency_id.id,
                'invoice_line_ids': [],
            }
            
            # Add invoice lines
            for line in self.line_ids:
                line_vals = {
                    'name': line.name or 'Invoice Line',
                    'quantity': line.quantity,
                    'price_unit': abs(line.price_unit) if move_type == 'in_refund' else line.price_unit,
                    'product_id': line.product_id.id if line.product_id else False,
                }
                
                # Apply VAT tax if rate is specified
                if line.vat_rate > 0:
                    # Find or create tax with the specified rate
                    tax = self.env['account.tax'].search([
                        ('type_tax_use', '=', 'purchase'),
                        ('amount', '=', line.vat_rate),
                        ('company_id', '=', self.company_id.id),
                    ], limit=1)
                    
                    if tax:
                        line_vals['tax_ids'] = [(6, 0, [tax.id])]
                    else:
                        _logger.warning(f"No purchase tax found with rate {line.vat_rate}% for company {self.company_id.name}")
                
                # Always use account 501000 (Spotreba materiálu) for all invoice lines
                account_501000 = self.env['account.account'].search([
                    ('code', '=', '501000'),
                ], limit=1)
                
                if account_501000:
                    line_vals['account_id'] = account_501000.id
                else:
                    # Fallback if account doesn't exist
                    _logger.warning(f"Account 501000 not found. Using default expense account.")
                    account = self.env['account.account'].search([
                        ('account_type', '=', 'expense'),
                    ], limit=1)
                    
                    if account:
                        line_vals['account_id'] = account.id
                
                invoice_vals['invoice_line_ids'].append((0, 0, line_vals))
            
            # Create invoice
            invoice = self.env['account.move'].create(invoice_vals)
            
            # Attach the PDF to the invoice
            self.attachment_id.res_model = 'account.move'
            self.attachment_id.res_id = invoice.id
            
            self.invoice_id = invoice.id
            self.state = 'done'
            
            self.message_post(body=_('Invoice %s created successfully.') % invoice.name)
            
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'res_id': invoice.id,
                'view_mode': 'form',
                'target': 'current',
            }
            
        except Exception as e:
            _logger.exception("Error creating invoice: %s", str(e))
            self.state = 'error'
            self.error_message = str(e)
            raise UserError(_('Error creating invoice: %s') % str(e))

    def action_view_invoice(self):
        """Open the created invoice"""
        self.ensure_one()
        
        if not self.invoice_id:
            raise UserError(_('No invoice has been created yet.'))
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.invoice_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_reset_to_draft(self):
        """Reset to draft state"""
        self.ensure_one()
        self.state = 'draft'
        self.error_message = False

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """
        Override to automatically create processor from incoming email.
        This is called when an email arrives at the alias email address.
        """
        if custom_values is None:
            custom_values = {}
        
        # Get email subject and sender
        subject = msg_dict.get('subject', 'Invoice from email')
        email_from = msg_dict.get('from', msg_dict.get('email_from', ''))
        
        # Create the processor record
        custom_values['name'] = _('New')
        custom_values['notes'] = f'Received from: {email_from}\nSubject: {subject}'
        processor = super().message_new(msg_dict, custom_values) 
        
        return processor
    
    def message_post(self, **kwargs):
        """Override to process attachments when message is posted"""
        res = super().message_post(**kwargs)
        
        # If this is a new record in draft state and has no attachment_id yet
        if self.state == 'draft' and not self.attachment_id:
            _logger.info(f"Checking for PDF attachments on record {self.id}")
            # Check if there are PDF attachments now
            pdf_attachments = self.env['ir.attachment'].search([
                ('res_model', '=', 'supplier.invoice.processor'),
                ('res_id', '=', self.id),
                ('mimetype', '=', 'application/pdf'),
            ])
            
            _logger.info(f"Found {len(pdf_attachments)} PDF attachments")
            
            if pdf_attachments and not self.attachment_id:
                # Use the first PDF found
                first_pdf = pdf_attachments[0]
                _logger.info(f"Processing PDF: {first_pdf.name}")
                self.write({
                    'attachment_id': first_pdf.id,
                    'pdf_filename': first_pdf.name,
                })
                
                # Automatically process the PDF
                try:
                    self.action_process_pdf()
                    _logger.info("PDF processing successful")
                    
                    # AUTO-CREATE INVOICE:
                    # - Always for refunds (skip pairing requirement)
                    # - For regular invoices: only if ALL lines are matched with products
                    if self.state == 'extracted':  # All lines are matched OR is_refund
                        _logger.info(f"Auto-creating invoice for {self.name} (refund={self.is_refund})")
                        self.action_create_invoice()
                    else:
                        # Some lines are unmatched - stay in pairing state
                        _logger.info(f"Unmatched lines detected. Staying in pairing state for {self.name}")
                        self.message_post(body=_('PDF načítané, ale niektoré riadky vyžadujú párovanie s produktom.\nPrejdite na "Nepárované riadky" a spárujte ich alebo vytvorte nové produkty.'))
                        
                except UserError as e:
                    # Send alert email for user errors (duplicates, etc.)
                    _logger.exception("User error during PDF processing: %s", str(e))
                    self._send_error_notification_email(str(e))
                    raise
                except Exception as e:
                    # Send alert email for other exceptions
                    _logger.exception("Auto-processing PDF failed: %s", str(e))
                    self._send_error_notification_email(str(e))
        
        return res
    
    def _send_error_notification_email(self, error_message):
        """Send email alert when invoice import fails or is duplicated"""
        self.ensure_one()
        
        try:
            # Prepare email body
            email_body = f'''
            <html>
            <body style="font-family: Arial, sans-serif; color: #333;">
                <h2 style="color: #d32f2f;">Chyba pri importe faktúry</h2>
                
                <h3>Podrobnosti:</h3>
                <table style="border-collapse: collapse; width: 100%; margin-bottom: 20px;">
                    <tr style="background-color: #ffebee;">
                        <td style="padding: 8px;"><b>Chybová správa:</b></td>
                        <td style="padding: 8px; color: #d32f2f;"><b>{error_message}</b></td>
                    </tr>
                    <tr style="background-color: #f5f5f5;">
                        <td style="padding: 8px;"><b>Zdrojový súbor:</b></td>
                        <td style="padding: 8px;">{self.filename or (self.attachment_id.name if self.attachment_id else 'N/A')}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px;"><b>Procesor ID:</b></td>
                        <td style="padding: 8px;">{self.name} (ID: {self.id})</td>
                    </tr>
                </table>
                
                <p style="color: #d32f2f;"><b>Vyžaduje sa manuálne riešenie!</b></p>
                <p>Prosím, skontrolujte záznam v systéme a vyrieďte problém.</p>
                <p style="font-size: 12px; color: #999;">Tento email bol generovaný automaticky pri neúspechu importe faktúry.</p>
            </body>
            </html>
            '''
            
            # Send email alert
            mail_values = {
                'email_from': self.env.company.email,
                'email_to': 'obrunovsky7@gmail.com,oliver.brunovsky@novem.sk,tomas.juricek@novem.sk',
                'subject': f'CHYBA: Import faktúry zlyhal - {self.filename or "Neznámy súbor"}',
                'body_html': email_body,
            }
            self.env['mail.mail'].create(mail_values).send()
            _logger.info(f"Error notification email sent for {self.name}")
            
        except Exception as e:
            _logger.warning(f"Failed to send error notification email: {str(e)}")


class SupplierInvoiceProcessorLine(models.Model):
    _name = 'supplier.invoice.processor.line'
    _description = 'Supplier Invoice Processor Line'
    _order = 'processor_id, sequence, id'

    processor_id = fields.Many2one(
        'supplier.invoice.processor',
        string='Processor',
        required=True,
        ondelete='cascade',
    )
    
    sequence = fields.Integer(
        string='Sequence',
        default=10,
    )
    
    name = fields.Char(
        string='Popis',
        required=True,
    )
    
    product_id = fields.Many2one(
        'product.product',
        string='Produkt',
    )
    
    quantity = fields.Float(
        string='Množstvo',
        default=1.0,
        required=True,
    )
    
    price_unit = fields.Float(
        string='Jednotková cena',
        required=True,
    )
    
    vat_rate = fields.Float(
        string='DPH %',
        default=0.0,
        help='Sadzba DPH v percentách (napr. 20 pre 20%)',
    )
    
    price_subtotal = fields.Float(
        string='Medzisúčet',
        compute='_compute_price_subtotal',
        store=True,
    )
    needs_pairing = fields.Boolean(
        string='Vyžaduje párovanie',
        compute='_compute_needs_pairing',
        store=True,
    )
    
    should_ignore = fields.Boolean(
        string='Ignorovať párovanie',
        compute='_compute_should_ignore',
        help='Táto línka sa ignoruje pri párovaní (napr. poplatky, doprava)',
    )
    
    currency_id = fields.Many2one(
        related='processor_id.currency_id',
        string='Mena',
        readonly=True,
    )

    @api.depends('quantity', 'price_unit')
    def _compute_price_subtotal(self):
        for line in self:
            line.price_subtotal = line.quantity * line.price_unit

    @api.depends('product_id', 'name', 'processor_id.supplier_id')
    def _compute_needs_pairing(self):
        for line in self:
            # If line should be ignored, it doesn't need pairing
            if line.should_ignore:
                line.needs_pairing = False
            else:
                # Otherwise, it needs pairing if it doesn't have a product
                line.needs_pairing = not bool(line.product_id)

    @api.depends('name', 'processor_id.supplier_id')
    def _compute_should_ignore(self):
        """Check if this line should be ignored based on ignore rules."""
        ignore_rule_model = self.env['pairing.ignore.rule']
        for line in self:
            supplier_id = line.processor_id.supplier_id.id if line.processor_id.supplier_id else None
            line.should_ignore = ignore_rule_model.should_ignore_line(line.name, supplier_id)

    @api.model
    def create(self, vals):
        line = super().create(vals)
        if line.processor_id:
            line.processor_id._update_pairing_state()
        return line

    def write(self, vals):
        res = super().write(vals)
        # If product was added/removed, refresh processor pairing state
        if 'product_id' in vals:
            for line in self:
                if line.processor_id:
                    line.processor_id._update_pairing_state()
        return res

    def unlink(self):
        processors = self.mapped('processor_id')
        res = super().unlink()
        for processor in processors:
            processor._update_pairing_state()
        return res
    
    def action_pair_with_product(self):
        """Open wizard to pair this line with a product"""
        self.ensure_one()
        return {
            'name': _('Spárovať s produktom'),
            'type': 'ir.actions.act_window',
            'res_model': 'product.pairing.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_line_id': self.id,
                'default_description': self.name,
                'default_supplier_id': self.processor_id.supplier_id.id if self.processor_id.supplier_id else False,
            },
        }
    
    def action_create_product(self):
        """Create a new product from this line and pair it, plus all matching unpaired lines"""
        self.ensure_one()
        
        # Use absolute value of price (in case of credit notes with negative prices)
        price = abs(self.price_unit)
        
        # Calculate price with 5% markup
        price_with_markup = price * 1.05
        
        # Create product with prices from invoice line
        product_template = self.env['product.template'].create({
            'name': self.name,
            'type': 'consu',  # Consumable product
            'purchase_ok': True,
            'sale_ok': True,
            'list_price': price_with_markup,  # Selling price with 5% markup
            'standard_price': price,  # Cost price (same as invoice)
        })
        
        # Get the product variant (product.product)
        product = product_template.product_variant_ids[0] if product_template.product_variant_ids else None
        
        if not product:
            raise UserError(_('Nepodarilo sa vytvoriť variantu produktu'))
        
        # Create pairing rule
        self.env['product.pairing.rule'].create_pairing(
            self.name,
            product.id,
            self.processor_id.supplier_id.id if self.processor_id.supplier_id else None,
        )
        
        # Pair current line
        self.product_id = product.id
        
        # Find and pair ALL matching unpaired lines with the same description
        matching_lines = self.env['supplier.invoice.processor.line'].search([
            ('name', '=', self.name),
            ('product_id', '=', False),
            ('id', '!=', self.id),
        ])
        
        matched_count = len(matching_lines)
        if matching_lines:
            matching_lines.write({'product_id': product.id})
        
        self.processor_id.message_post(
            body=_('Nový produkt vytvorený: %s (Nákladová cena: %s, Predajná cena: %s %s). Spárovaných riadkov: %d') % (
                product.name,
                price,
                price_with_markup,
                self.processor_id.currency_id.name,
                matched_count + 1
            )
        )
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Produkt vytvorený a spárovaný'),
                'message': _('Produkt "%s" bol vytvorený s cenou %s %s.\nSpárovaných celkom: %d riadkov.') % (
                    product.name,
                    price,
                    self.processor_id.currency_id.name,
                    matched_count + 1
                ),
                'type': 'success',
            },
        }
    
    def action_mark_ignore(self):
        """Mark this line description as one to ignore in pairing"""
        self.ensure_one()
        
        # Create or update ignore rule for this description
        ignore_rule_model = self.env['pairing.ignore.rule']
        supplier_id = self.processor_id.supplier_id.id if self.processor_id.supplier_id else None
        
        ignore_rule = ignore_rule_model.create_ignore_rule(
            description=self.name,
            supplier_id=supplier_id,
            match_type='contains'
        )
        
        self.processor_id.message_post(
            body=_('Popis "%s" bol pridaný do pravidiel na ignorovanie. Podobné riadky sa už nebudú zobrazovať pri párovaní.') % self.name
        )
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Pravidlo na ignorovanie vytvorené'),
                'message': _('Popis "%s" sa bude ignorovať pri párovaní.') % self.name,
                'type': 'success',
            },
        }
