#!/usr/bin/env python3
"""
Test script for Supplier Invoice Processor

This script demonstrates how to use the supplier invoice processor programmatically.
It can be used for testing or automation purposes.

Usage:
    python test_invoice_processor.py --pdf /path/to/invoice.pdf --supplier "Supplier Name"
"""

import argparse
import base64
import sys


def test_processor(env, pdf_path, supplier_name=None):
    """
    Test the invoice processor with a PDF file
    
    Args:
        env: Odoo environment
        pdf_path: Path to PDF file
        supplier_name: Optional supplier name
    """
    
    # Read PDF file
    with open(pdf_path, 'rb') as f:
        pdf_data = f.read()
    
    # Create attachment
    attachment = env['ir.attachment'].create({
        'name': pdf_path.split('/')[-1],
        'type': 'binary',
        'datas': base64.b64encode(pdf_data),
        'mimetype': 'application/pdf',
    })
    
    print(f"✓ Created attachment: {attachment.name}")
    
    # Create processor record
    processor = env['supplier.invoice.processor'].create({
        'attachment_id': attachment.id,
    })
    
    print(f"✓ Created processor: {processor.name}")
    
    # Process PDF
    print("⏳ Processing PDF...")
    processor.action_process_pdf()
    
    print(f"✓ PDF processed, state: {processor.state}")
    print(f"  Extracted {len(processor.line_ids)} lines")
    
    if processor.supplier_id:
        print(f"  Identified supplier: {processor.supplier_id.name}")
    
    if processor.invoice_number:
        print(f"  Invoice number: {processor.invoice_number}")
    
    if processor.invoice_date:
        print(f"  Invoice date: {processor.invoice_date}")
    
    print(f"  Total amount: {processor.total_amount} {processor.currency_id.name}")
    
    # Display extracted lines
    if processor.line_ids:
        print("\n📋 Extracted Lines:")
        for idx, line in enumerate(processor.line_ids, 1):
            product_info = f" [{line.product_id.name}]" if line.product_id else ""
            print(f"  {idx}. {line.name}{product_info}")
            print(f"     Qty: {line.quantity}, Price: {line.price_unit}, Subtotal: {line.price_subtotal}")
    
    # Optionally set supplier if provided
    if supplier_name and not processor.supplier_id:
        supplier = env['res.partner'].search([
            ('name', 'ilike', supplier_name),
            ('supplier_rank', '>', 0),
        ], limit=1)
        
        if supplier:
            processor.supplier_id = supplier.id
            print(f"\n✓ Set supplier: {supplier.name}")
        else:
            print(f"\n⚠ Supplier '{supplier_name}' not found")
    
    # Create invoice if ready
    if processor.state == 'extracted' and processor.supplier_id:
        print("\n⏳ Creating invoice...")
        result = processor.action_create_invoice()
        
        if processor.invoice_id:
            print(f"✓ Invoice created: {processor.invoice_id.name}")
            print(f"  Amount: {processor.invoice_id.amount_total} {processor.invoice_id.currency_id.name}")
            print(f"  State: {processor.invoice_id.state}")
            print(f"  Lines: {len(processor.invoice_id.invoice_line_ids)}")
        else:
            print("✗ Failed to create invoice")
    else:
        print("\n⚠ Not ready to create invoice:")
        if not processor.supplier_id:
            print("  - No supplier set")
        if processor.state != 'extracted':
            print(f"  - Wrong state: {processor.state}")
    
    return processor


def main():
    """Main entry point for command-line usage"""
    parser = argparse.ArgumentParser(description='Test Supplier Invoice Processor')
    parser.add_argument('--pdf', required=True, help='Path to PDF file')
    parser.add_argument('--supplier', help='Supplier name (optional)')
    parser.add_argument('--db', default='odoo', help='Database name')
    parser.add_argument('--config', help='Odoo config file')
    
    args = parser.parse_args()
    
    try:
        # This would need to be run within Odoo context
        print("Note: This script must be run within Odoo context (odoo shell)")
        print("Example: odoo shell -d your_db --config=/path/to/odoo.conf")
        print("")
        print("Then run:")
        print(f"  from contract.test_invoice_processor import test_processor")
        print(f"  test_processor(env, '{args.pdf}', '{args.supplier}')")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    # Check if running in Odoo shell
    try:
        # If 'env' exists, we're in odoo shell
        env  # noqa: F821
        print("Running in Odoo shell context")
        
        # Example usage
        # test_processor(env, '/path/to/invoice.pdf', 'Supplier Name')
        
    except NameError:
        # Not in Odoo shell, show usage instructions
        main()
