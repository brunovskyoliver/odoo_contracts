from odoo import models, fields, api
import csv
import os
import logging
from datetime import datetime
import unicodedata

class Nameday(models.Model):
    _name = 'nameday.nameday'
    _description = 'Nameday Calendar'

    date = fields.Date(string='Date', required=True)
    name = fields.Char(string='Name', required=True)
    normalized_name = fields.Char(string='Normalized Name', required=True)

    @api.model
    def _normalize_name(self, name):
        """Remove accents and convert to lowercase."""
        # Convert to NFKD form and remove diacritics
        name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('ASCII')
        # Convert to lowercase
        return name.lower().strip()

    @api.model
    def _load_namedays(self):
        # Clear existing records
        self.search([]).unlink()
        
        # Get the path to the CSV file
        module_path = os.path.dirname(os.path.dirname(__file__))
        csv_path = os.path.join(module_path, 'sk-meniny_.csv')
        
        current_year = fields.Date.today().year
        
        with open(csv_path, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) == 2:
                    date_str, name = row[0].strip(), row[1].strip()
                    try:
                        # Convert MM-DD to full date with current year
                        date = datetime.strptime(f"{current_year}-{date_str}", "%Y-%m-%d").date()
                        self.create({
                            'date': date,
                            'name': name,
                            'normalized_name': self._normalize_name(name)
                        })
                    except ValueError:
                        continue

    @api.model
    def _send_nameday_emails(self):
        today = fields.Date.today()
        _logger = logging.getLogger(__name__)
        
        # Find today's nameday
        nameday = self.search([('date', '=', today)], limit=1)
        if not nameday:
            _logger.info("No nameday found for today: %s", today)
            return

        _logger.info("Today's nameday (%s) is: %s (normalized: %s)", 
                    today, nameday.name, nameday.normalized_name)

        # Find partners who have this name
        Partner = self.env['res.partner']
        all_partners = Partner.search([])
        matching_partners = Partner.browse()
        
        # Compare normalized names
        for partner in all_partners:
            if not partner.name:  # Skip partners with no name
                continue
                
            partner_names = partner.name.split()  # Split full name into parts
            normalized_partner_names = [self._normalize_name(n) for n in partner_names]
            
            # Check if any part of the partner's name matches the nameday
            if nameday.normalized_name in normalized_partner_names:
                matching_partners |= partner
                _logger.info("Found matching partner: %s (normalized names: %s)", 
                           partner.name, normalized_partner_names)
        
        if not matching_partners:
            _logger.info("No matching partners found for nameday: %s", nameday.name)
            return

        # Filter partners with emails and track unique emails
        email_sent = set()
        unique_partners = self.env['res.partner'].browse()
        
        for partner in matching_partners:
            if not partner.email:
                _logger.info("Skipping partner %s - no email address", partner.name)
                continue
                
            if partner.email.lower() in email_sent:
                _logger.info("Skipping partner %s - email %s already processed", 
                           partner.name, partner.email)
                continue
                
            email_sent.add(partner.email.lower())
            unique_partners |= partner
        
        if not unique_partners:
            _logger.info("No partners with unique emails found for nameday: %s", nameday.name)
            return
            
        _logger.info("Sending nameday emails to unique recipients: %s", 
                    ', '.join(f"{p.name} ({p.email})" 
                            for p in unique_partners))
                            
        # Get the email template
        template = self.env.ref('contract.email_template_nameday')
        
        # Send email to each unique partner
        for partner in unique_partners:
            _logger.info("Sending nameday email to: %s (%s)", partner.name, partner.email)
            template.send_mail(partner.id, force_send=True)
            
        _logger.info("Completed sending nameday emails")

        return True
