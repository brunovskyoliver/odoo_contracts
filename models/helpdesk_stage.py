# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class HelpdeskStage(models.Model):
    _inherit = "helpdesk.stage"

    _CUSTOMER_CARE_TEAM_NAME = "Starostlivosť o zákazníka"
    _NEW_STAGE_NAME = "Nové"
    _SCHEDULING_STAGE_NAME = "Scheduling"
    _SILENT_RESOLVED_STAGE_NAME = "Vyriešené bez oznámenia"
    _RESOLVED_STAGE_NAME = "Vyriešené"

    def _get_customer_care_team(self):
        return self.env["helpdesk.team"].search(
            [("name", "=", self._CUSTOMER_CARE_TEAM_NAME)],
            limit=1,
        )

    def _get_exact_customer_care_stage(self, stage_name):
        team = self._get_customer_care_team()
        if not team:
            return self.browse()
        candidates = self.search([
            ("name", "=", stage_name),
            ("team_ids", "in", team.ids),
        ])
        return candidates.filtered(
            lambda stage: set(stage.team_ids.ids) == {team.id}
        )[:1]

    def _get_customer_care_stage(self, stage_name):
        team = self._get_customer_care_team()
        if not team:
            return self.browse()
        candidates = self.search([
            ("name", "=", stage_name),
            ("team_ids", "in", team.ids),
        ])
        exact_stage = self._get_exact_customer_care_stage(stage_name)
        return exact_stage or candidates.filtered(
            lambda stage: team in stage.team_ids
        )[:1]

    def _get_silent_resolved_stage(self, target_stage):
        team_ids = set(target_stage.team_ids.ids)
        candidates = self.search([
            ("name", "=", self._SILENT_RESOLVED_STAGE_NAME),
            ("team_ids", "in", list(team_ids)),
        ])
        return candidates.filtered(
            lambda stage: set(stage.team_ids.ids) == team_ids
        )[:1]

    @api.model
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

    @api.model
    def create_or_update_customer_care_scheduling_stage(self):
        team = self._get_customer_care_team()
        if not team:
            _logger.warning(
                "No helpdesk team named %s found while creating the Scheduling stage.",
                self._CUSTOMER_CARE_TEAM_NAME,
            )
            return True

        new_stage = self._get_customer_care_stage(self._NEW_STAGE_NAME)
        scheduling_stage = self._get_exact_customer_care_stage(
            self._SCHEDULING_STAGE_NAME
        )
        values = {
            "active": True,
            "description": False,
            "fold": False,
            "name": self._SCHEDULING_STAGE_NAME,
            "sequence": (new_stage.sequence + 1) if new_stage else 1,
            "team_ids": [(6, 0, team.ids)],
            "template_id": False,
        }
        if new_stage:
            values.update({
                "legend_blocked": new_stage.legend_blocked,
                "legend_done": new_stage.legend_done,
                "legend_normal": new_stage.legend_normal,
            })

        if scheduling_stage:
            scheduling_stage.write(values)
        else:
            self.create(values)
        return True
