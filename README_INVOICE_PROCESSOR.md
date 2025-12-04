# Supplier Invoice Processor

## Overview

The Supplier Invoice Processor is a powerful tool that automates the extraction of invoice data from PDF files and creates supplier invoices in Odoo.

## Features

- **PDF Upload**: Upload supplier invoice PDFs directly to the system
- **Automatic Data Extraction**: Automatically extracts:
  - Supplier information (VAT, name)
  - Invoice number
  - Invoice and due dates
  - Invoice line items (description, quantity, price)
  - Total amounts
- **Manual Review & Edit**: Review and edit extracted data before creating the invoice
- **Automatic Invoice Creation**: Creates supplier invoices (account.move) with one click
- **Product Matching**: Attempts to match extracted descriptions with existing products
- **Supplier Creation**: Automatically creates new suppliers if not found in the system

## Installation

### Requirements

Install the required Python libraries:

```bash
pip install PyPDF2>=3.0.0 pdfplumber>=0.10.0
```

Or install from the requirements.txt:

```bash
pip install -r requirements.txt
```

### Odoo Installation

1. Update the module in Odoo
2. The feature will be accessible under **Accounting > Vendors > Invoice Processor**

## Usage

### Basic Workflow

1. **Upload PDF**
   - Navigate to Accounting > Vendors > Invoice Processor
   - Click "Create"
   - Attach a supplier invoice PDF file

2. **Process PDF**
   - Click the "Process PDF" button
   - The system will extract data from the PDF
   - Review the extracted information:
     - Supplier details
     - Invoice number and dates
     - Invoice line items

3. **Review & Edit**
   - Verify the supplier is correctly identified
   - Check and edit invoice lines as needed
   - Match products or leave blank to create new ones
   - Adjust quantities and prices if necessary

4. **Create Invoice**
   - Once satisfied with the data, click "Create Invoice"
   - A supplier invoice will be created with the extracted data
   - The PDF will be automatically attached to the invoice

### Features by Status

- **Draft**: Initial state, ready to process PDF
- **Processing**: PDF is being processed
- **Extracted**: Data extracted successfully, ready to create invoice
- **Done**: Invoice created successfully
- **Error**: An error occurred during processing

### PDF Extraction Capabilities

The processor can extract:

- **Invoice Header**:
  - Invoice number (patterns: Invoice #, Faktura č., etc.)
  - Dates (DD.MM.YYYY, DD/MM/YYYY, YYYY-MM-DD formats)
  - Supplier VAT/IČO/DIČ
  - Supplier name

- **Invoice Lines**:
  - Product/service descriptions
  - Quantities
  - Unit prices
  - Subtotals

- **Totals**:
  - Untaxed amount
  - Tax amount
  - Total amount

### Customization

The PDF parsing logic is in the `supplier_invoice_processor.py` model. You can customize the extraction patterns in these methods:

- `_parse_invoice_data()`: Main parsing logic
- `_parse_table_lines()`: Table extraction from PDF
- `_parse_lines_from_text()`: Fallback text-based line extraction

Common customizations:
- Add new regex patterns for your invoice format
- Adjust column detection in table extraction
- Customize supplier/product matching logic

## Technical Details

### Models

#### supplier.invoice.processor
Main model that manages the processing workflow.

**Key Fields**:
- `attachment_id`: PDF file attachment
- `supplier_id`: Identified/selected supplier
- `invoice_number`: Extracted invoice number
- `invoice_date`: Extracted invoice date
- `line_ids`: Extracted invoice lines
- `invoice_id`: Created supplier invoice reference

**Key Methods**:
- `action_process_pdf()`: Extract data from PDF
- `action_create_invoice()`: Create supplier invoice from extracted data
- `_extract_text_from_pdf()`: PDF text extraction
- `_parse_invoice_data()`: Parse structured data from text

#### supplier.invoice.processor.line
Invoice line items extracted from PDF.

**Key Fields**:
- `name`: Product/service description
- `product_id`: Matched product (optional)
- `quantity`: Quantity
- `price_unit`: Unit price
- `price_subtotal`: Calculated subtotal

### Security

Access rights are configured for two groups:
- **Accounting Manager**: Full access (create, read, write, delete)
- **Billing User**: Read, create, write (no delete)

### Menu Location

The processor is accessible at:
**Accounting > Vendors > Invoice Processor**

## Troubleshooting

### PDF Not Processing

- Ensure PyPDF2 and pdfplumber are installed
- Check the PDF is not password-protected or corrupted
- Review the "Extracted Text" tab to see what was extracted
- Check the "Error" tab for specific error messages

### No Data Extracted

- The PDF format may not match the expected patterns
- Customize the regex patterns in `_parse_invoice_data()`
- Use pdfplumber's table extraction for structured invoices
- Check the "Extracted Text" tab to see the raw text

### Supplier Not Found

- The processor tries to match by VAT number
- If not found, it will create a new supplier (if name is extracted)
- You can manually select the supplier before creating the invoice

### Products Not Matched

- Products are matched by name similarity
- If no match is found, leave product_id empty
- The invoice line will be created without a product reference
- You can manually assign products after invoice creation

## Future Enhancements

Possible improvements:
- AI/ML-based extraction using OCR
- Support for multiple invoice formats/templates
- Batch processing of multiple PDFs
- Integration with email (fetch attachments from dodavatelia@novem.sk)
- Automatic approval workflow
- Invoice validation rules
- Multi-language support

## Support

For issues or questions, please contact your Odoo administrator.
