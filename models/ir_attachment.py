from odoo import models

class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def action_get_attachment_tree_view(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % self.id,
            'target': 'self',
        }
