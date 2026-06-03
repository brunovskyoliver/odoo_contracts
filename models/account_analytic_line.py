# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    helpdesk_ticket_id = fields.Many2one(
        comodel_name="helpdesk.ticket",
        string="Helpdesk tiket",
        index=True,
        copy=False,
    )
    timer_session_id = fields.Many2one(
        comodel_name="contract.timer.session",
        string="Záznam časovača",
        index=True,
        copy=False,
        readonly=True,
    )
    timer_raw_hours = fields.Float(
        string="Skutočné hodiny časovača",
        copy=False,
        readonly=True,
        digits=(16, 4),
    )
    timer_rounded_hours = fields.Float(
        string="Zaokrúhlené hodiny časovača",
        copy=False,
        readonly=True,
        digits=(16, 2),
    )
    timer_double_rate = fields.Boolean(
        string="2x sadzba časovača",
        copy=False,
        readonly=True,
    )
