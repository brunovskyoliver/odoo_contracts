#!/bin/bash
# Installation script for Supplier Invoice Processor

echo "=================================================="
echo "Supplier Invoice Processor - Installation Script"
echo "=================================================="
echo ""

# Check if pip is available
if ! command -v pip3 &> /dev/null && ! command -v pip &> /dev/null; then
    echo "❌ Error: pip is not installed"
    echo "Please install pip first: sudo apt-get install python3-pip"
    exit 1
fi

# Determine pip command
if command -v pip3 &> /dev/null; then
    PIP_CMD="pip3"
else
    PIP_CMD="pip"
fi

echo "Using pip command: $PIP_CMD"
echo ""

# Install Python dependencies
echo "📦 Installing Python dependencies..."
echo ""

$PIP_CMD install PyPDF2>=3.0.0
if [ $? -eq 0 ]; then
    echo "✓ PyPDF2 installed successfully"
else
    echo "✗ Failed to install PyPDF2"
    exit 1
fi

$PIP_CMD install pdfplumber>=0.10.0
if [ $? -eq 0 ]; then
    echo "✓ pdfplumber installed successfully"
else
    echo "✗ Failed to install pdfplumber"
    exit 1
fi

echo ""
echo "=================================================="
echo "✅ Installation completed successfully!"
echo "=================================================="
echo ""
echo "Next steps:"
echo "1. Restart Odoo service"
echo "2. Go to Apps > Update Apps List"
echo "3. Find 'Recurring - Contracts Management'"
echo "4. Click 'Upgrade'"
echo "5. Access at: Accounting > Vendors > Invoice Processor"
echo ""
echo "Documentation:"
echo "  - Full docs: README_INVOICE_PROCESSOR.md"
echo "  - Quick start: QUICKSTART_INVOICE_PROCESSOR.md"
echo "  - Summary: IMPLEMENTATION_SUMMARY.md"
echo ""
echo "Happy processing! 🚀"
