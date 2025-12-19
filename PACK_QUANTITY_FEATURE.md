# Pack Quantity Feature Implementation

## Overview
Added support for product packs with quantity multipliers. This allows you to pair products like "u7-pro" with pack products like "u7-pro-pack" and specify how many units are in each pack (e.g., 5 units).

## Changes Made

### 1. ProductPairingRule Model (`models/product_pairing_rule.py`)
- **Added field**: `pack_qty` (Integer, default=1)
  - Stores the quantity multiplier for pack products
  - Example: Set to 5 for a 5-pack product
  
- **Updated method**: `create_pairing()`
  - Now accepts `pack_qty` parameter
  - Updates existing pairings with new pack_qty values

### 2. ProductPairingWizard Model (`wizards/product_pairing_wizard.py`)
- **Added field**: `pack_qty` (Integer, default=1)
  - User can select the pack quantity when pairing products
  
- **Updated method**: `action_pair()`
  - Multiplies invoice line quantity by pack_qty
  - Applies multiplier to all matching lines when "apply_to_all" is selected
  - Creates/updates pairing rule with pack_qty information
  - Updated success message to show the multiplier applied

### 3. Pairing Wizard View (`wizards/product_pairing_wizard_views.xml`)
- Added `pack_qty` field to the form view
- Positioned next to product selection for easy configuration

### 4. Pairing Rule Views (`views/product_pairing_rule_views.xml`)
- Updated form view to display `pack_qty` field
- Added `pack_qty` to list view for visibility

### 5. SupplierInvoiceProcessor Model (`models/supplier_invoice_processor.py`)
- **Updated method**: `_find_product()`
  - Now returns tuple: `(product_id, pack_qty)` instead of just `product_id`
  - Retrieves pack_qty from matching pairing rules
  - Falls back to pack_qty=1 if no pairing rule found
  
- **Updated method**: `_process_pdf_invoice()`
  - Unpacks the tuple from `_find_product()`
  - Multiplies invoice line quantity by pack_qty automatically
  - Quantity multiplication only applied when pairing rule exists

## Usage Example

### Scenario: U7 Pro 5-Pack

1. **Create pairing** in the invoice processor:
   - Description: "Ubiquiti U7 Pro (5-PACK)"
   - Product: "u7-pro" (single unit product)
   - Pack Qty: 5
   - Check "Remember pairing" to save the rule

2. **Result**:
   - If invoice shows: 1 x "Ubiquiti U7 Pro (5-PACK)"
   - System will create: 5 x "u7-pro" in the invoice

3. **Auto-apply** with "Apply to all similar rows":
   - All matching invoice lines will be paired
   - Each will have quantity multiplied by 5

### Manual Pairing Rule Maintenance

Users can:
1. Navigate to: **Accounting > Payables > Product Pairing Rules**
2. View all pairing rules with their pack quantities
3. Edit existing rules to change pack_qty
4. See usage statistics and last used date

## Technical Details

- **Backward compatible**: Default pack_qty is 1, so existing pairings work unchanged
- **Automatic application**: Pack quantities are applied automatically when processing invoices
- **Manual override**: Users can still manually pair products in the wizard with custom pack quantities
- **Statistics**: Usage count and last used date tracking maintained

## Files Modified

1. `/models/product_pairing_rule.py` - Added pack_qty field and logic
2. `/models/supplier_invoice_processor.py` - Updated product finding and line creation
3. `/wizards/product_pairing_wizard.py` - Added pack_qty selection and application
4. `/wizards/product_pairing_wizard_views.xml` - Updated wizard UI
5. `/views/product_pairing_rule_views.xml` - Updated rule views

## Testing Recommendations

1. Create a test invoice with a pack product
2. Pair it with pack_qty=5
3. Verify quantity is multiplied correctly
4. Test "Apply to all" with multiple pack items
5. Verify pairing rule is saved and reused on subsequent invoices
