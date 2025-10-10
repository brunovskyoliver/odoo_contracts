# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import csv
import base64
from io import StringIO

class ContractInventoryImport(models.TransientModel):
    _name = 'contract.inventory.import.wizard'
    _description = 'Import Inventory Data'

    import_file = fields.Binary(string='Import File (CSV)', required=True)
    filename = fields.Char(string='Filename')
    
    def action_import(self):
        if not self.import_file:
            raise UserError(_("Please select a file to import"))
            
        # Decode and read CSV file
        csv_data = base64.b64decode(self.import_file).decode('utf-8')
        csv_file = StringIO(csv_data)
        reader = csv.reader(csv_file)
        
        # Statistics
        stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'partners_not_found': set(),
            'products_not_found': set()
        }
        
        # Process each line
        for row in reader:
            stats['total'] += 1
            if len(row) != 3:
                continue
                
            partner_name, quantity, product_name = row
            
            # Find partner
            partner = self.env['res.partner'].search([
                ('name', 'ilike', partner_name)
            ], limit=1)
            
            if not partner:
                stats['partners_not_found'].add(partner_name)
                stats['failed'] += 1
                continue
                
            # Find product
            product = self.env['product.product'].search([
                ('name', 'ilike', product_name)
            ], limit=1)
            
            if not product:
                stats['products_not_found'].add(product_name)
                stats['failed'] += 1
                continue
                
            # Find or create inventory
            inventory = self.env['contract.inventory'].search([
                ('partner_id', '=', partner.id)
            ], limit=1)
            
            if not inventory:
                inventory = self.env['contract.inventory'].create({
                    'name': f"Import Inventory - {partner.name}",
                    'partner_id': partner.id,
                })
            
            # Create inventory line without stock movement
            try:
                self.env['contract.inventory.line'].with_context(no_stock_movement=True).create({
                    'inventory_id': inventory.id,
                    'product_id': product.id,
                    'quantity': float(quantity),
                    'state': 'assigned'
                })
                stats['success'] += 1
            except Exception as e:
                stats['failed'] += 1
                
        # Create message body
        email_body = f"""Import completed:
- Total lines: {stats['total']}
- Successfully imported: {stats['success']}
- Failed: {stats['failed']}
"""
        if stats['partners_not_found']:
            email_body += "\nPartners not found:\n" + "\n".join(f"- {p}" for p in stats['partners_not_found'])
        if stats['products_not_found']:
            email_body += "\nProducts not found:\n" + "\n".join(f"- {p}" for p in stats['products_not_found'])

        # Send email with notifications disabled
        mail_values = {
            'subject': _('Contract Inventory Import Report - %s') % fields.Datetime.now(),
            'email_from': self.env.company.email or self.env.user.email,
            'email_to': self.env.user.email,
            'body_html': f'<pre>{email_body}</pre>',
            'auto_delete': False,
        }
        
        # Send the email
        self.env['mail.mail'].sudo().with_context(
            mail_notify_force_send=False,
            mail_auto_subscribe_no_notify=True,
            tracking_disable=True,
            mail_create_nolog=True
        ).create(mail_values).send()

        # Show success notification and close wizard
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': _('Import bol dokončený. Podrobná správa bola odoslaná na email.'),
                'type': 'success' if stats['success'] > 0 else 'warning',
                'title': _('Import Dokončený'),
                'sticky': True,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }