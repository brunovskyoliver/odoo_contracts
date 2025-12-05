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
            
            # Extract text
            extracted_text = self._extract_text_from_pdf(pdf_data)
            self.extracted_text = extracted_text
            
            # Parse invoice data
            invoice_data = self._parse_invoice_data(extracted_text, pdf_data)
            
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
                self.env['supplier.invoice.processor.line'].create({
                    'processor_id': self.id,
                    'name': line_data.get('description'),
                    'quantity': line_data.get('quantity', 1.0),
                    'price_unit': line_data.get('price_unit', 0.0),
                    'vat_rate': line_data.get('vat_rate', 0.0),
                    'product_id': self._find_product(line_data.get('description')),
                })
            
            self.state = 'extracted'
            self.message_post(body=_('PDF načítalo %s riadkov.') % len(self.line_ids))
            
        except Exception as e:
            _logger.exception("Error processing PDF: %s", str(e))
            self.state = 'error'
            self.error_message = str(e)
            raise UserError(_('Error processing PDF: %s') % str(e))
        
        return True

    def _extract_text_from_pdf(self, pdf_data):
        """Extract text from PDF using pdfplumber or PyPDF2"""
        if not pdf_data:
            raise UserError(_('No PDF data found.'))
        
        text = ""
        
        if pdfplumber:
            try:
                import io
                pdf_file = io.BytesIO(pdf_data)
                with pdfplumber.open(pdf_file) as pdf:
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
        
        if not is_alza and not is_westech:
            raise UserError(_('This processor only handles Alza.sk and Westech invoices. Please check the PDF file.'))
        
        # Check if this is a credit note (dobropis/opravný doklad)
        is_refund = 'Opravný daňový doklad' in text or 'Dobropis' in text
        
        data = {
            'lines': [],
            'is_alza': is_alza,
            'is_westech': is_westech,
            'is_refund': is_refund,
        }
        if is_alza:
            data.update({ 'supplier_id': 21,})
        elif is_westech:
            data.update({ 'supplier_id': 1583,})
        
        # Extract invoice number (common patterns)
        invoice_patterns = [
            r'Opravný\s+daňový\s+doklad\s*-\s*(\d+)',  # Alza credit note / corrective invoice number
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
        
        # Find the tax breakdown section - support both comma and dot decimals
        vat_breakdown_pattern = r'(\d+)\s*%\s+([\d\s]+[,\.]\d+)\s+([\d\s]+[,\.]\d+)'
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
            r'Celkom k úhrade\s*:?\s*€?\s*([\d\s,]+\.?\d*)',  # WESTech format
            r'Celkom\s*:?\s*€?\s*([\d\s,]+\.?\d*)\s*EUR',
            r'Total\s*:?\s*€?\s*([\d\s,]+\.?\d*)',
            r'Amount\s*:?\s*€?\s*([\d\s,]+\.?\d*)',
        ]
        for pattern in amount_patterns:
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
        if pdfplumber:
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
        if not data['lines'] or len(data['lines']) > 20:  # Too many lines usually means bad parsing
            if data.get('is_westech'):
                data['lines'] = self._parse_westech_lines_from_text(text)
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
        
        # Pattern to identify product data line: Qty (int) Price (decimal) Price (decimal)
        # Example: "1 837,02 837,02 192,51 23 1 029,53 24"
        PRODUCT_DATA_PATTERN = re.compile(r'\s+(\d+)\s+(-?\d+,\d+)\s+(-?\d+,\d+)')
        CODE_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{2,}')  # Product codes like NA626e, SL190r

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

            # Check if this line contains product data (Qty Price Price...)
            data_match = PRODUCT_DATA_PATTERN.search(line)
            
            if data_match:
                # This line has product data - it's a product line
                # Everything before the numbers is the description
                desc_part = line[:data_match.start()].strip()
                
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
                    if PRODUCT_DATA_PATTERN.search(next_line):
                        break
                    
                    # This is a continuation line - add to description
                    # Remove any leading codes/numbers from continuation
                    parts = next_line.split()
                    if parts and re.search(r'\d', parts[0]):
                        # First token has digits, skip it
                        if len(parts) > 1:
                            continuation_lines.append(' '.join(parts[1:]))
                    else:
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
                
                # Skip lines with "Nehmotný produkt"
                if "Nehmotný produkt" in full_description:
                    _logger.info(f"Skipping intangible product: {full_description[:50]}")
                    i += 1
                    continue
                
                # Extract quantity and price from the match
                qty = float(data_match.group(1))
                price = float(data_match.group(2).replace(',', '.'))
                
                # Extract VAT rate from remaining numbers
                # After Qty Price Price, pattern is: DPH_Amount VAT% Total Warranty
                remaining = line[data_match.end():]
                tokens = remaining.split()
                vat_rate = 0.0
                
                for token in tokens:
                    if token.isdigit():
                        num = int(token)
                        if 0 <= num <= 25:
                            vat_rate = float(num)
                            break
                
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
                                "price_unit": price,
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

    def _find_product(self, description):
        """Try to find existing product by name"""
        if not description:
            return False
        
        product = self.env['product.product'].search([
            ('name', 'ilike', description)
        ], limit=1)
        
        return product.id if product else False

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
                    'price_unit': line.price_unit,
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
                
                # If product is set, get account from product
                if line.product_id:
                    accounts = line.product_id.product_tmpl_id.get_product_accounts()
                    if accounts and accounts.get('expense'):
                        line_vals['account_id'] = accounts['expense'].id
                
                # If no account set yet, try to find default expense account
                if 'account_id' not in line_vals:
                    # Try different approaches to find an expense account
                    account = self.env['account.account'].search([
                        ('account_type', '=', 'expense'),
                    ], limit=1)
                    
                    if not account:
                        # Fallback: search for any account with "expense" in internal_group
                        account = self.env['account.account'].search([
                            ('internal_group', '=', 'expense'),
                        ], limit=1)
                    
                    if not account:
                        # Last resort: find any account with code starting with 5 (expense accounts in Slovak COA)
                        account = self.env['account.account'].search([
                            ('code', '=like', '5%'),
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
        
        processor = super().message_new(msg_dict, custom_values=custom_values)
        
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
                except Exception as e:
                    _logger.exception("Auto-processing PDF failed: %s", str(e))
        
        return res


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
    
    currency_id = fields.Many2one(
        related='processor_id.currency_id',
        string='Mena',
        readonly=True,
    )

    @api.depends('quantity', 'price_unit')
    def _compute_price_subtotal(self):
        for line in self:
            line.price_subtotal = line.quantity * line.price_unit
