# O2 Slovakia Invoice PDF Processing

## Overview

The Supplier Invoice Processor now supports O2 Slovakia invoice PDFs. The parser extracts service and usage line items from O2 invoices for automatic processing.

## Features

- **Automatic O2 Detection**: Identifies O2 Slovakia invoices by company name
- **First Page Processing**: Processes the first page of PDF (user requirement)
- **Service Extraction**: Extracts telecom services (plans, VPN, etc.)
- **Usage Extraction**: Extracts usage items (calls, SMS, data, etc.)
- **Duplicate Prevention**: Avoids duplicate service entries
- **Smart Parsing**: Handles multiple invoice formats with fallback patterns

## O2 Invoice Format

O2 invoices contain:

### Main Services (Monthly Plans)
```
Service Name | Period Start - Period End | Amount EUR
e-Net Fér bez dát  01.11.2025 - 30.11.2025  0.15€
```

### Usage/Call Items
```
Service Name | Amount EUR
Volania do ostatných sietí SR  0.2207€
```

### Special Items (with volume)
```
Service Name | Amount | Volume | Unit | Usage Amount
Prenesené dáta v SR (MB)  31.0  10191534080.0  Byte  0.0€
```

## Parsing Rules

1. **Monthly Services**: Lines with date ranges (dd.mm.yyyy - dd.mm.yyyy)
   - Pattern: `ServiceName  DateStart - DateEnd  Amount`
   - Extracted as single-unit services

2. **Usage Items**: Lines with amounts but no date range
   - Pattern: `ServiceName  Amount`
   - Includes calls, SMS, data, VPN charges

3. **Duplicate Handling**: 
   - Tracks seen service names to prevent duplicates
   - Useful when invoice has summary sections

4. **Summary Skipping**: 
   - Stops parsing at "Rekapitulácia DPH" section
   - Ignores metadata lines (email, web, ICO, etc.)

## VAT Rate

All O2 services default to **20% VAT** (standard Slovak rate for telecom).

## Usage

### In Odoo

1. Navigate to **Accounting > Vendors > Invoice Processor**
2. Click **Create**
3. Upload O2 PDF file
4. Click **Process PDF** to extract data
5. Review extracted services
6. Adjust product matches if needed
7. Click **Create Invoice** to finalize

### Programmatic

```python
processor = env['supplier.invoice.processor'].create({
    'pdf_file': pdf_data,  # Base64 encoded PDF
    'pdf_filename': 'o2_invoice.pdf'
})
processor.action_process_pdf()
```

## Sample O2 Invoice Structure

```
Faktúra č. 7100133985
Dátum vystavenia: 02.12.2025
Dátum splatnosti: 16.12.2025

NOVEM IT s.r.o.
IČO: 50282859
DIČ: 2120427078

Názov položky                              Fakturčné obdobie           Suma

MOBILNÝ HLAS
NOVEM IT s.r.o.                           01.11.2025 - 30.11.2025
  e-Net Fér bez dát                       01.11.2025 - 30.11.2025    0.15€
  Mesačný poplatok VPN                    01.11.2025 - 30.11.2025    0.03€
  Volania do ostatných sietí SR                                       0.2207€
  ...

Rekapitulácia DPH
DPH 0%:   53.02€ (99.69€)
DPH 20%: 1094.24€ (1430.82€)

Celkem k úhradě: 1366.11€
```

## Supported Formats

- ✓ Monthly service plans
- ✓ Recurring charges (VPN, data)
- ✓ Usage-based charges (calls, SMS)
- ✓ Data volume charges
- ✓ Additional services

## Limitations

- **PDF Only**: Processes PDF format (not CSV)
- **First Page**: Only first page of multi-page invoices (as requested)
- **Text-based**: Requires text extraction (not scanned images)
- **Date Parsing**: Expects DD.MM.YYYY format
- **Amount Format**: Expects comma or dot as decimal separator

## Troubleshooting

### No Services Extracted
- Check PDF is text-based (not scanned image)
- Verify O2 Slovakia name in header for automatic detection
- Check that amounts have valid format (digits with comma/dot)

### Duplicate Services
- The processor automatically deduplicates within same extraction
- Review extracted services before creating invoice

### Wrong VAT Rate
- O2 services default to 20% VAT
- Can be adjusted manually after extraction
- Some special services (parking, etc.) may need 0% VAT

## Technical Details

### Detection
O2 invoices are detected by:
- "O2 Slovakia" or "O2 sk" in document text
- Case-insensitive matching
- Assigned supplier_id: 1653

### Parsing Algorithm
1. Split PDF text into lines
2. Apply monthly service pattern (with date range)
3. Apply simple amount pattern (without date)
4. Skip summary sections
5. Remove duplicates
6. Return service list

### VAT Handling
- Default: 20% for all telecom services
- Configurable per line in Odoo UI
- Can be overridden during invoice creation

## Future Enhancements

- Support for multi-page invoices
- Variable VAT rates based on service type
- CSV export of extracted items
- Batch processing of multiple invoices
- OCR support for scanned PDFs

## Integration

The O2 processor integrates with:
- **supplier_invoice_processor**: Main extraction module
- **product.pairing.rule**: Service-to-product mapping
- **account.move**: Invoice creation
- **res.partner**: Supplier lookup/creation

## Support

For issues or feature requests:
- Check syntax with: `mcp_pylance_mcp_s_pylanceFileSyntaxErrors`
- Review extraction with: Process PDF and check extracted_text field
- Debug patterns by examining `_parse_o2_lines_from_text()` method
