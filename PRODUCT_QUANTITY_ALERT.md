# Product Quantity Alert System

## Overview
This system automatically monitors product stock levels and sends email notifications when stock falls below configured thresholds. Alerts are sent daily but respect frequency limits to avoid alert fatigue.

## Features

✓ **Per-Product Configuration**: Each product can have its own alert settings
✓ **Flexible Alerting**: Configure minimum stock level and alert frequency (weekly)
✓ **Smart Deduplication**: Alerts won't repeat daily - respects configured frequency
✓ **Easy Toggle**: Enable/disable alerts with a single checkbox per product
✓ **Slovak Interface**: All user-visible labels and descriptions in Slovak
✓ **Daily Automatic Checks**: Cron job runs daily to check stock levels
✓ **Email Notifications**: Sends formatted HTML emails with product details

## Configuration

### Company-Level Settings
1. Navigate to **Settings → Companies → Company Name**
2. Set the **Quantity Alert Email** field with recipient address(es)
   - Use comma to separate multiple recipients: `email1@example.com,email2@example.com`

### Product-Level Settings
1. Open a **Product** form
2. Go to the **"Upozornenie na zásoby"** (Quantity Alert) tab
3. Configure:
   - **Enable Quantity Alerts**: Toggle to enable/disable alerts for this product
   - **Minimum Stock Quantity**: Alert triggers when stock < this value (default: 2)
   - **Alert Frequency per Week**: Max alerts per week (default: 1 = once per week)

## How It Works

### Daily Check Process
1. **Cron Job** runs daily at 08:00 AM
2. **System checks** all products with alerts enabled
3. For each product with stock below minimum:
   - Checks if alert limit for the week is not exceeded
   - Sends email notification if conditions are met
   - Tracks alert sent with timestamp and weekly count

### Alert Frequency Logic
- **Once per week (default)**: Maximum 1 alert per product per week
- **Twice per week**: Maximum 2 alerts per product per week
- **etc.**
- Resets on Sunday (week start)

### Email Content
Emails include:
- Product name
- Current stock quantity
- Minimum threshold
- Date of alert
- Warning message in Slovak

## Database Tables

### product.template (Extended)
- `alert_qty_enabled` (Boolean): Enable/disable alerts
- `minimum_alert_qty` (Integer): Minimum stock threshold
- `alert_frequency_per_week` (Integer): Max alerts per week

### product.quantity.alert (New)
- `product_id` (Many2one): Related product
- `last_alert_date` (Datetime): When last alert was sent
- `alert_count_this_week` (Integer): Alerts sent in current week

### res.company (Extended)
- `quantity_alert_email` (Char): Email recipient(s)

## Technical Details

### Models
- **ProductQuantityAlert**: Handles alert logic and email sending
  - Method: `_check_and_send_quantity_alerts()`
  - Method: `_send_quantity_alert_email(product, alert_record)`

### Cron Job
- **Name**: "Check Product Quantity Alerts"
- **Frequency**: Daily
- **Time**: 08:00 AM
- **Record**: `ir_cron_product_quantity_alert`

### Views
- **Product Template Form**: Extended with quantity alert page
- **Company Settings**: Email configuration field

## Testing the System

### Manual Alert Test
```python
# In Odoo console:
alert_model = env['product.quantity.alert']
alert_model._check_and_send_quantity_alerts()
```

### Setup Example
1. Enable alerts on product: "Mobile Phone"
   - Minimum Qty: 5
   - Frequency: 1 per week

2. Configure company email:
   - Quantity Alert Email: `admin@example.com,warehouse@example.com`

3. Reduce phone stock to 3 units
4. Cron job will send email next day at 08:00
5. No more emails until next week (unless frequency allows)

## Troubleshooting

### Emails Not Sending
- Check company `quantity_alert_email` is configured
- Verify product has `alert_qty_enabled = True`
- Check stock is actually below minimum
- Review email server configuration in Odoo

### Too Many/Few Alerts
- Adjust `alert_frequency_per_week` on product
- Check `last_alert_date` in product.quantity.alert records
- Verify week reset logic (Sunday)

### Cron Not Running
- Check Settings → Technical → Automation → Scheduled Actions
- Verify `ir_cron_product_quantity_alert` is active
- Check server logs: `/var/log/odoo/odoo-server.log`

## Development Notes

### Code Locations
- Model: `/models/product_quantity_alert.py`
- Product Fields: `/models/product_template.py`
- Company Fields: `/models/res_company.py`
- Views: `/views/product_template_view.xml`
- Cron: `/data/product_quantity_alert_cron.xml`
- Manifest: `/__manifest__.py`

### Language
- Code: English
- User Interface: Slovak
- Logging: English (for debugging)

## Future Enhancements
- Dashboard showing products below threshold
- Customizable email templates
- Alert history report
- Webhook notifications
- Different alert levels (warning, critical)
- Supplier-specific alerts
