# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    scheduled_for = fields.Datetime(
        string="Naplánované na",
        copy=False,
        tracking=True,
    )
    schedule_recurring = fields.Boolean(
        string="Opakovať",
        copy=False,
        tracking=True,
    )
    schedule_interval = fields.Integer(
        string="Opakovať každých",
        default=1,
        copy=False,
    )
    schedule_interval_type = fields.Selection(
        [
            ("days", "Dni"),
            ("months", "Mesiace"),
            ("years", "Roky"),
        ],
        string="Jednotka opakovania",
        default="months",
        copy=False,
    )
    schedule_published = fields.Boolean(
        string="Zverejnené plánovačom",
        copy=False,
        readonly=True,
    )
    is_customer_care_team = fields.Boolean(
        compute="_compute_is_customer_care_team",
        export_string_translation=False,
    )
    show_schedule_fields = fields.Boolean(
        compute="_compute_show_schedule_fields",
        export_string_translation=False,
    )

    @api.depends("team_id.name")
    def _compute_is_customer_care_team(self):
        team_name = self.env["helpdesk.stage"]._CUSTOMER_CARE_TEAM_NAME
        for ticket in self:
            ticket.is_customer_care_team = ticket.team_id.name == team_name

    @api.depends("team_id.name", "stage_id.name", "schedule_published")
    def _compute_show_schedule_fields(self):
        stage_model = self.env["helpdesk.stage"]
        for ticket in self:
            is_customer_care_ticket = (
                ticket.team_id.name == stage_model._CUSTOMER_CARE_TEAM_NAME
            )
            is_scheduling_stage = (
                ticket.stage_id.name == stage_model._SCHEDULING_STAGE_NAME
            )
            is_published_to_new = (
                ticket.schedule_published
                and ticket.stage_id.name == stage_model._NEW_STAGE_NAME
            )
            ticket.show_schedule_fields = (
                is_customer_care_ticket
                and (is_scheduling_stage or is_published_to_new)
            )

    @api.constrains("schedule_recurring", "schedule_interval", "scheduled_for")
    def _check_schedule_recurrence(self):
        for ticket in self:
            if not ticket.schedule_recurring:
                continue
            if not ticket.scheduled_for:
                raise ValidationError(
                    _("Naplánované na je povinné pre opakované plánované tickety.")
                )
            if ticket.schedule_interval <= 0:
                raise ValidationError(
                    _("Opakovať každých musí byť väčšie ako nula pre opakované plánované tickety.")
                )

    def _track_template(self, changes):
        if (
            self.env.context.get("skip_helpdesk_stage_template")
            and "stage_id" in changes
        ):
            if isinstance(changes, set):
                changes = set(changes)
                changes.discard("stage_id")
            else:
                changes = [
                    field_name for field_name in changes if field_name != "stage_id"
                ]
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

    def _get_recurrence_delta(self, interval=None):
        self.ensure_one()
        interval = interval if interval is not None else self.schedule_interval
        interval = max(interval or 1, 1)
        interval_type = self.schedule_interval_type or "months"
        delta_by_type = {
            "days": relativedelta(days=interval),
            "months": relativedelta(months=interval),
            "years": relativedelta(years=interval),
        }
        return delta_by_type[interval_type]

    def _get_next_scheduled_for(self):
        self.ensure_one()
        interval = self.schedule_interval or 1
        if interval <= 0:
            interval = 1
        base_date = self.scheduled_for or fields.Datetime.now()
        delta = self._get_recurrence_delta(interval=interval)
        next_scheduled_for = base_date + delta
        now = fields.Datetime.now()
        while next_scheduled_for <= now:
            next_scheduled_for += delta
        return next_scheduled_for

    def _reschedule_recurring_customer_care_tickets(self):
        stage_model = self.env["helpdesk.stage"]
        scheduling_stage = stage_model._get_customer_care_stage(
            stage_model._SCHEDULING_STAGE_NAME
        )
        if not scheduling_stage:
            _logger.warning(
                "No Scheduling stage found for helpdesk team %s while rescheduling recurring tickets.",
                stage_model._CUSTOMER_CARE_TEAM_NAME,
            )
            return True

        for ticket in self:
            next_scheduled_for = ticket._get_next_scheduled_for()
            ticket.with_context(
                skip_helpdesk_stage_template=True,
                skip_customer_care_recurring_reschedule=True,
            ).write({
                "schedule_published": False,
                "scheduled_for": next_scheduled_for,
                "stage_id": scheduling_stage.id,
            })
        return True

    def _get_recurring_tickets_to_reschedule(self, target_stage):
        stage_model = self.env["helpdesk.stage"]
        if target_stage.name != stage_model._RESOLVED_STAGE_NAME:
            return self.browse()
        return self.filtered(
            lambda ticket: ticket.schedule_recurring
            and ticket.team_id.name == stage_model._CUSTOMER_CARE_TEAM_NAME
        )

    def _write_silent_resolved_redirect(self, vals):
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
            result &= tickets.with_context(
                skip_helpdesk_stage_template=True,
                skip_silent_resolved_redirect=True,
            ).write(dict(vals, stage_id=target_stage.id))

        remaining_tickets = self - processed_tickets
        if remaining_tickets:
            result &= super(HelpdeskTicket, remaining_tickets).write(vals)

        return result

    def write(self, vals):
        if "stage_id" not in vals:
            return super().write(vals)

        stage_model = self.env["helpdesk.stage"]
        target_stage = stage_model.browse(vals["stage_id"])
        if (
            not self.env.context.get("skip_silent_resolved_redirect")
            and target_stage.name == stage_model._SILENT_RESOLVED_STAGE_NAME
        ):
            return self._write_silent_resolved_redirect(vals)

        result = super().write(vals)
        if self.env.context.get("skip_customer_care_recurring_reschedule"):
            return result

        tickets_to_reschedule = self._get_recurring_tickets_to_reschedule(target_stage)
        if tickets_to_reschedule:
            tickets_to_reschedule._reschedule_recurring_customer_care_tickets()
        return result

    @api.model
    def cron_publish_scheduled_customer_care_tickets(self):
        stage_model = self.env["helpdesk.stage"]
        stage_model.create_or_update_customer_care_scheduling_stage()
        customer_care_teams = stage_model._get_customer_care_teams()
        scheduling_stage = stage_model._get_customer_care_stage(
            stage_model._SCHEDULING_STAGE_NAME
        )
        scheduling_stages = stage_model._get_customer_care_stage_candidates(
            stage_model._SCHEDULING_STAGE_NAME
        )
        new_stage = stage_model._get_customer_care_stage(stage_model._NEW_STAGE_NAME)
        if not customer_care_teams or not scheduling_stage or not new_stage:
            _logger.warning(
                "Skipping scheduled ticket publishing because Scheduling or Nové stage is missing for team %s.",
                stage_model._CUSTOMER_CARE_TEAM_NAME,
            )
            return True

        due_tickets = self.search([
            ("active", "=", True),
            ("team_id", "in", customer_care_teams.ids),
            ("stage_id", "in", scheduling_stages.ids),
            ("scheduled_for", "!=", False),
            ("scheduled_for", "<=", fields.Datetime.now()),
        ])
        recurring_tickets = due_tickets.filtered("schedule_recurring")
        one_time_tickets = due_tickets - recurring_tickets
        if recurring_tickets:
            recurring_tickets.write({
                "schedule_published": True,
                "stage_id": new_stage.id,
            })
        if one_time_tickets:
            one_time_tickets.write({
                "schedule_published": True,
                "scheduled_for": False,
                "stage_id": new_stage.id,
            })
        return True
