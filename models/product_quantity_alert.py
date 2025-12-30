# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, fields, api
import logging
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)


class ProductQuantityAlert(models.Model):
    _name = 'product.quantity.alert'
    _description = 'Product Quantity Alert Tracking'
    _rec_name = 'product_id'

    product_id = fields.Many2one(
        'product.template',
        string='Product',
        required=True,
        ondelete='cascade'
    )
    last_alert_date = fields.Datetime(
        string='Last Alert Date',
        help='When the last quantity alert was sent for this product'
    )
    alert_count_this_week = fields.Integer(
        string='Alerts This Week',
        default=0,
        help='Number of alerts sent this week'
    )

    @api.model
    def _check_and_send_quantity_alerts(self):
        """
        Check all products with quantity alerts enabled.
        Send email if quantity is below minimum and alert frequency allows it.
        """
        _logger.info("Starting quantity alert check")
        
        # Get all products with alerts enabled
        products = self.env['product.template'].search([
            ('alert_qty_enabled', '=', True)
        ])

        if not products:
            _logger.info("No products with enabled quantity alerts found")
            return

        _logger.info(f"Found {len(products)} products with alerts enabled")

        today = fields.Date.today()
        week_start = today - timedelta(days=today.weekday())

        for product in products:
            try:
                # Get current stock quantity
                stock_qty = product.qty_available

                # Check if quantity is below minimum
                if stock_qty < product.minimum_alert_qty:
                    _logger.info(
                        f"Product {product.name}: Stock {stock_qty} < Minimum {product.minimum_alert_qty}"
                    )

                    # Check alert frequency
                    alert_record = self.search([
                        ('product_id', '=', product.id)
                    ], limit=1)

                    should_send = False

                    if not alert_record:
                        # First time alert for this product
                        should_send = True
                        _logger.info(f"First alert for product {product.name}")
                    else:
                        last_date = alert_record.last_alert_date
                        if last_date:
                            last_date = fields.Datetime.from_string(last_date)
                        else:
                            should_send = True

                        if last_date:
                            # Calculate days since last alert
                            days_since_alert = (datetime.now() - last_date).days
                            
                            # Calculate alerts this week
                            if last_date.date() >= week_start:
                                alert_count = alert_record.alert_count_this_week
                            else:
                                alert_count = 0

                            # Check if we can send based on frequency
                            max_alerts_per_week = product.alert_frequency_per_week
                            
                            if alert_count < max_alerts_per_week:
                                should_send = True
                                _logger.info(
                                    f"Product {product.name}: Can send alert "
                                    f"({alert_count}/{max_alerts_per_week} this week)"
                                )
                            else:
                                _logger.info(
                                    f"Product {product.name}: Alert limit reached "
                                    f"({alert_count}/{max_alerts_per_week} this week)"
                                )

                    if should_send:
                        self._send_quantity_alert_email(product, alert_record)

            except Exception as e:
                _logger.error(f"Error checking product {product.name}: {str(e)}")

        _logger.info("Quantity alert check completed")

    def _send_quantity_alert_email(self, product, alert_record):
        """Send email alert for low product quantity"""
        try:
            _logger.info(f"Sending quantity alert for product: {product.name}")

            # Hardcoded email recipient
            email_to = 'obrunovsky7@gmail.com'

            # Get company email for sender
            company = self.env.company

            # Prepare email body in Slovak
            email_body = f"""
            <p>Pozor! Nízka zásoba produktu</p>
            <p><strong>Produkt:</strong> {product.name}</p>
            <p><strong>Aktuálna zásoby:</strong> {product.qty_available}</p>
            <p><strong>Minimálna zásoby:</strong> {product.minimum_alert_qty}</p>
            <p><strong>Dátum kontroly:</strong> {fields.Date.today()}</p>
            <p>Prosím, zvážte objednanie tohto produktu.</p>
            """

            mail_values = {
                'email_from': company.email,
                'email_to': email_to,
                'subject': f'Upozornenie na nízku zásobu: {product.name}',
                'body_html': email_body,
            }

            mail = self.env['mail.mail'].create(mail_values)
            mail.send()

            _logger.info(f"Email sent for product: {product.name}")

            # Update alert tracking
            today = fields.Date.today()
            week_start = today - timedelta(days=today.weekday())

            if not alert_record:
                # Create new alert record
                self.create({
                    'product_id': product.id,
                    'last_alert_date': fields.Datetime.now(),
                    'alert_count_this_week': 1,
                })
                _logger.info(f"Created new alert record for product: {product.name}")
            else:
                # Update existing alert record
                if alert_record.last_alert_date:
                    last_date = fields.Datetime.from_string(alert_record.last_alert_date)
                    if last_date.date() >= week_start:
                        new_count = alert_record.alert_count_this_week + 1
                    else:
                        new_count = 1
                else:
                    new_count = 1

                alert_record.write({
                    'last_alert_date': fields.Datetime.now(),
                    'alert_count_this_week': new_count,
                })
                _logger.info(
                    f"Updated alert record for product: {product.name} "
                    f"(alerts this week: {new_count})"
                )

        except Exception as e:
            _logger.error(f"Error sending quantity alert email: {str(e)}")
