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
    _TRANSLATION_LANGS = ("en_US", "sk_SK")

    def _get_customer_care_teams(self):
        teams = self.env["helpdesk.team"].browse()
        for lang in self._TRANSLATION_LANGS:
            teams |= self.env["helpdesk.team"].with_context(lang=lang).search([
                ("name", "=", self._CUSTOMER_CARE_TEAM_NAME),
            ])
        teams |= self.env["helpdesk.team"].search([
            ("name", "=", self._CUSTOMER_CARE_TEAM_NAME),
        ])
        return teams

    def _get_customer_care_team(self):
        preferred_team = self.env["helpdesk.team"].browse(1).exists()
        if preferred_team and (
            "active" not in preferred_team._fields or preferred_team.active
        ):
            return preferred_team
        return self._get_customer_care_teams()[:1]

    def _get_stage_candidates_by_name(self, stage_name):
        stages = self.browse()
        for lang in self._TRANSLATION_LANGS:
            stages |= self.with_context(lang=lang).search([
                ("name", "=", stage_name),
            ])
        stages |= self.search([("name", "=", stage_name)])
        return stages

    def _get_customer_care_stage_candidates(self, stage_name):
        teams = self._get_customer_care_teams()
        if not teams:
            return self.browse()
        return self._get_stage_candidates_by_name(stage_name).filtered(
            lambda stage: any(team in stage.team_ids for team in teams)
        )

    def _get_exact_customer_care_stage(self, stage_name):
        teams = self._get_customer_care_teams()
        if not teams:
            return self.browse()
        candidates = self._get_customer_care_stage_candidates(stage_name)
        team_ids = set(teams.ids)
        return candidates.filtered(
            lambda stage: set(stage.team_ids.ids) == team_ids
        )[:1]

    def _get_customer_care_stage(self, stage_name):
        teams = self._get_customer_care_teams()
        if not teams:
            return self.browse()
        candidates = self._get_customer_care_stage_candidates(stage_name)
        exact_stage = self._get_exact_customer_care_stage(stage_name)
        if exact_stage:
            return exact_stage
        team_ids = set(teams.ids)
        stage_with_all_teams = candidates.filtered(
            lambda stage: team_ids.issubset(set(stage.team_ids.ids))
        )[:1]
        return stage_with_all_teams or candidates[:1]

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
        teams = self._get_customer_care_teams()
        if not teams:
            _logger.warning(
                "No helpdesk team named %s found while creating the Scheduling stage.",
                self._CUSTOMER_CARE_TEAM_NAME,
            )
            return True

        new_stage = self._get_customer_care_stage(self._NEW_STAGE_NAME)
        customer_care_team_ids = set(teams.ids)
        scheduling_stages = self._get_stage_candidates_by_name(
            self._SCHEDULING_STAGE_NAME
        ).filtered(
            lambda stage: not stage.team_ids
            or bool(customer_care_team_ids.intersection(stage.team_ids.ids))
        )
        scheduling_stage = self._get_exact_customer_care_stage(
            self._SCHEDULING_STAGE_NAME
        )
        if not scheduling_stage and scheduling_stages:
            scheduling_stage = max(
                scheduling_stages,
                key=lambda stage: (
                    len(customer_care_team_ids.intersection(stage.team_ids.ids)),
                    -stage.id,
                ),
            )
        values = {
            "active": True,
            "description": False,
            "fold": False,
            "name": self._SCHEDULING_STAGE_NAME,
            "sequence": (new_stage.sequence + 1) if new_stage else 1,
            "team_ids": [(6, 0, teams.ids)],
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
            scheduling_stage = self.create(values)

        duplicate_stages = scheduling_stages - scheduling_stage
        if duplicate_stages:
            tickets = self.env["helpdesk.ticket"].search([
                ("stage_id", "in", duplicate_stages.ids),
            ])
            if tickets:
                tickets.with_context(
                    skip_helpdesk_stage_template=True,
                    skip_customer_care_recurring_reschedule=True,
                ).write({"stage_id": scheduling_stage.id})
            duplicate_stages.unlink()
        return True
