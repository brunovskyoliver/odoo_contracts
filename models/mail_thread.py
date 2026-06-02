# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, models


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    @api.model
    def _message_route_process(self, message, message_dict, routes):
        thread_id = super()._message_route_process(message, message_dict, routes)
        self._contract_create_helpdesk_ticket_from_inbound_message(message_dict)
        return thread_id

    @api.model
    def _contract_create_helpdesk_ticket_from_inbound_message(self, message_dict):
        if "helpdesk.ticket" not in self.env:
            return False

        message_id = message_dict.get("message_id")
        if not message_id:
            return False

        source_messages = self.env["mail.message"].sudo().search([
            ("message_id", "=", message_id),
            ("model", "!=", False),
            ("res_id", "!=", False),
            ("message_type", "=", "email"),
        ])
        source_messages = source_messages.filtered(
            lambda message: message.model != "helpdesk.ticket"
        )
        if not source_messages:
            return False

        ticket = self.env["helpdesk.ticket"]._contract_create_from_inbound_email(
            message_dict,
            source_model=source_messages[0].model,
            source_res_id=source_messages[0].res_id,
            force_create_partner=True,
        )
        return bool(ticket)
