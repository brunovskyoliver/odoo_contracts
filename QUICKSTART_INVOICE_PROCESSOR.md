# Supplier Invoice Processor - Quick Start Guide

## Installation

### 1. Install Python Dependencies

```bash
# Navigate to the contract module directory
cd /var/lib/odoo/addons/contract

# Install required packages
pip install -r requirements.txt
```

Or install individually:
```bash
pip install PyPDF2 pdfplumber
```

### 2. Update Odoo Module

- Go to Apps
- Update Apps List
- Find "Recurring - Contracts Management"
- Click "Upgrade"

## Quick Start

### Step 1: Access the Processor

Navigate to: **Accounting > Vendors > Invoice Processor**

### Step 2: Upload a PDF

1. Click **Create**
2. Click on the **PDF Attachment** field
3. Select your supplier invoice PDF file
4. Save

### Step 3: Process the PDF

1. Click **Process PDF** button
2. Wait for processing to complete
3. Status will change to "Extracted"

### Step 4: Review Extracted Data

Check the following tabs:

- **Main tab**: Supplier, invoice number, dates, totals
- **Invoice Lines**: Extracted line items (description, quantity, price)
- **Extracted Text**: Raw text from PDF (for debugging)

### Step 5: Adjust if Needed

Before creating the invoice, you can:

- ✏️ Select or change the supplier
- ✏️ Edit invoice lines (descriptions, quantities, prices)
- ✏️ Match products from your catalog
- ✏️ Add or remove lines

### Step 6: Create Invoice

1. Click **Create Invoice** button
2. The supplier invoice will be created
3. Click **View Invoice** to see the created invoice
4. The PDF is automatically attached to the invoice

## Example Workflow

```
1. Upload PDF → 2. Process PDF → 3. Review Data → 4. Create Invoice ✓
```

## Common Scenarios

### Scenario 1: Simple Invoice (All Data Extracted)

1. Upload PDF
2. Click "Process PDF"
3. Verify supplier and amounts
4. Click "Create Invoice"
✓ Done!

### Scenario 2: Manual Supplier Selection

1. Upload PDF
2. Click "Process PDF"
3. Select supplier manually from dropdown
4. Click "Create Invoice"
✓ Done!

### Scenario 3: Editing Line Items

1. Upload PDF
2. Click "Process PDF"
3. Go to "Invoice Lines" tab
4. Edit quantities or prices
5. Match products if needed
6. Click "Create Invoice"
✓ Done!

### Scenario 4: Error Recovery

If status is "Error":
1. Check the "Error" tab for details
2. Click "Reset to Draft"
3. Edit data manually
4. Click "Create Invoice" (skips processing)

## Tips & Tricks

### 💡 Tip 1: Batch Processing
Process multiple invoices by:
1. Creating multiple processor records
2. Processing them one by one
3. Using the tree view to track status

### 💡 Tip 2: Reusing Extracted Data
If processing fails:
- The extracted text is saved
- You can manually enter data based on it
- Then create the invoice

### 💡 Tip 3: Product Matching
- Products are matched by name
- Create products beforehand for better matching
- Use consistent naming in your product catalog

### 💡 Tip 4: Supplier Auto-Creation
- If supplier VAT is extracted but not in system
- A new supplier will be created automatically
- Review and update supplier details after

## Supported PDF Formats

The processor works best with:
- ✓ Text-based PDFs (searchable)
- ✓ Invoices with clear table structure
- ✓ Standard invoice formats

May have issues with:
- ✗ Scanned PDFs (images) - requires OCR
- ✗ Password-protected PDFs
- ✗ Heavily formatted or complex layouts

## Keyboard Shortcuts

- **Alt+C**: Create (new processor)
- **Alt+E**: Edit mode
- **Alt+S**: Save
- **Ctrl+K**: Discard changes

## Troubleshooting

### Problem: "No data extracted"
**Solution**: 
- Check "Extracted Text" tab to see raw text
- PDF might be image-based (needs OCR)
- Customize extraction patterns for your format

### Problem: "Wrong amounts extracted"
**Solution**:
- Manually edit the amounts in the form
- Invoice can still be created

### Problem: "Supplier not found"
**Solution**:
- Select supplier manually from dropdown
- Or let system create new supplier automatically

### Problem: "Products not matched"
**Solution**:
- Leave product field empty (line created without product)
- Or manually select products after processing
- Or create products in catalog first

## Next Steps

After creating your first invoice:
1. Review the created invoice
2. Post the invoice if correct
3. Process payment when received
4. Archive the processor record (optional)

## Video Tutorial

*(Coming soon)*

## Need Help?

- Check the full documentation: `README_INVOICE_PROCESSOR.md`
- Contact your Odoo administrator
- Submit issues to development team

---

**Happy Processing! 🚀**
