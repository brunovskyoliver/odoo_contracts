# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models


class HelpdeskStage(models.Model):
    _inherit = "helpdesk.stage"

    _SILENT_RESOLVED_STAGE_NAME = "Vyriešené bez oznámenia"
    _RESOLVED_STAGE_NAME = "Vyriešené"

    def _get_silent_resolved_stage(self, target_stage):
        team_ids = set(target_stage.team_ids.ids)
        candidates = self.search([
            ("name", "=", self._SILENT_RESOLVED_STAGE_NAME),
            ("team_ids", "in", list(team_ids)),
        ])
        return candidates.filtered(lambda stage: set(stage.team_ids.ids) == team_ids)[:1]

    def create_or_update_silent_resolved_stages(self):
        resolved_stages = self.search([
            ("name", "=", self._RESOLVED_STAGE_NAME),
        ])
        for stage in resolved_stages:
            values = {
                "active": stage.active,
                "description": stage.description,
                "fold": False,
                "legend_blocked": stage.legend_blocked,
                "legend_done": stage.legend_done,
                "legend_normal": stage.legend_normal,
                "name": self._SILENT_RESOLVED_STAGE_NAME,
                "sequence": max(stage.sequence - 1, 0),
                "team_ids": [(6, 0, stage.team_ids.ids)],
                "template_id": False,
            }
            silent_stage = self._get_silent_resolved_stage(stage)
            if silent_stage:
                silent_stage.write(values)
            else:
                self.create(values)
        return True
