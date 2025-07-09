# Copyright 2025 NOVEM IT s.r.o.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class FollowupLine(models.Model):
    _inherit = 'account_followup.followup.line'
    _description = 'Follow-up Criteria'

    # We need this field for backwards compatibility
    auto_execute = fields.Boolean(string='Auto Execute', default=False)
    
    server_action_id = fields.Many2one(
        'ir.actions.server',
        string='Server Action',
        help='Server action to execute when this follow-up level is processed',
        domain=[('model_id.model', '=', 'account.move')],
    )

    def execute_followup(self, partners):
        res = super().execute_followup(partners)
        for line in self:
            if line.server_action_id and line.auto_execute:
                # Get the related moves for this followup line
                moves = self.env['account.move'].search([
                    ('partner_id', 'in', partners.ids),
                    ('state', '=', 'posted'),
                    ('payment_state', 'in', ['not_paid', 'partial']),
                ])
                if moves:
                    # Execute the server action with the moves as context
                    line.server_action_id.with_context(
                        active_model='account.move',
                        active_ids=moves.ids,
                    ).run()
        return res
