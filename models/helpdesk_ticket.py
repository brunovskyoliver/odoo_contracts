# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    def _track_template(self, changes):
        if self.env.context.get("skip_helpdesk_stage_template") and "stage_id" in changes:
            if isinstance(changes, set):
                changes = set(changes)
                changes.discard("stage_id")
            else:
                changes = [field_name for field_name in changes if field_name != "stage_id"]
        return super()._track_template(changes)

    def _get_resolved_stage_by_team(self):
        teams = self.mapped("team_id")
        resolved_stages = self.env["helpdesk.stage"].search([
            ("name", "=", self.env["helpdesk.stage"]._RESOLVED_STAGE_NAME),
            ("team_ids", "in", teams.ids),
        ])
        return {
            team.id: resolved_stages.filtered(lambda stage: team in stage.team_ids)[:1]
            for team in teams
        }

    def write(self, vals):
        if (
            "stage_id" not in vals
            or self.env.context.get("skip_silent_resolved_redirect")
        ):
            return super().write(vals)

        silent_stage = self.env["helpdesk.stage"].browse(vals["stage_id"])
        if silent_stage.name != self.env["helpdesk.stage"]._SILENT_RESOLVED_STAGE_NAME:
            return super().write(vals)

        resolved_stage_by_team = self._get_resolved_stage_by_team()
        processed_tickets = self.env["helpdesk.ticket"]
        result = True
        for team in self.mapped("team_id"):
            tickets = self.filtered(lambda ticket: ticket.team_id == team)
            target_stage = resolved_stage_by_team.get(team.id)
            if not tickets:
                continue
            if not target_stage:
                _logger.warning(
                    "No target resolved stage found for helpdesk team %s while redirecting silent resolved stage.",
                    team.display_name,
                )
                continue
            processed_tickets |= tickets
            result &= super(
                HelpdeskTicket,
                tickets.with_context(
                    skip_helpdesk_stage_template=True,
                    skip_silent_resolved_redirect=True,
                ),
            ).write(dict(vals, stage_id=target_stage.id))

        remaining_tickets = self - processed_tickets
        if remaining_tickets:
            result &= super(HelpdeskTicket, remaining_tickets).write(vals)

        return result
