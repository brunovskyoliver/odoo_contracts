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
    contract_source_message_id = fields.Char(
        string="Source Email Message-Id",
        copy=False,
        index=True,
        readonly=True,
    )
    contract_source_model = fields.Char(
        string="Source Model",
        copy=False,
        readonly=True,
    )
    contract_source_res_id = fields.Integer(
        string="Source Record ID",
        copy=False,
        readonly=True,
    )
    timer_timesheet_ids = fields.One2many(
        comodel_name="account.analytic.line",
        inverse_name="helpdesk_ticket_id",
        string="Pracovné výkazy časovača",
    )
    timer_timesheet_count = fields.Integer(
        string="Pracovné výkazy časovača",
        compute="_compute_timer_timesheet_count",
    )

    @api.depends("timer_timesheet_ids")
    def _compute_timer_timesheet_count(self):
        count_by_ticket = {ticket.id: 0 for ticket in self}
        timesheets = self.env["account.analytic.line"].search([
            ("timer_session_id", "!=", False),
            "|",
            "|",
            ("helpdesk_ticket_id", "in", self.ids),
            ("task_id.helpdesk_ticket_id", "in", self.ids),
            ("timer_session_id.helpdesk_ticket_id", "in", self.ids),
        ])
        for timesheet in timesheets:
            ticket = (
                timesheet.helpdesk_ticket_id
                or timesheet.task_id.helpdesk_ticket_id
                or timesheet.timer_session_id.helpdesk_ticket_id
            )
            if ticket.id in count_by_ticket:
                count_by_ticket[ticket.id] += 1
        for ticket in self:
            ticket.timer_timesheet_count = count_by_ticket.get(ticket.id, 0)

    def action_view_timer_timesheets(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "hr_timesheet.timesheet_action_all"
        )
        action["name"] = _("Pracovné výkazy časovača")
        action["domain"] = [
            ("timer_session_id", "!=", False),
            "|",
            "|",
            ("helpdesk_ticket_id", "=", self.id),
            ("task_id.helpdesk_ticket_id", "=", self.id),
            ("timer_session_id.helpdesk_ticket_id", "=", self.id),
        ]
        action["context"] = {
            **self.env.context,
            "default_helpdesk_ticket_id": self.id,
        }
        return action

    @api.model
    def _contract_is_external_inbound_email(self, msg_dict):
        if msg_dict.get("message_type") and msg_dict.get("message_type") != "email":
            return False
        if msg_dict.get("is_bounce") or msg_dict.get("bounced_email"):
            return False
        if (msg_dict.get("subject") or "").strip().lower() == "fwd to processor 3000":
            return False

        email_from = msg_dict.get("from") or msg_dict.get("email_from") or ""
        email_from_lower = email_from.lower()
        ignored_senders = (
            "mailer-daemon",
            "postmaster",
            "mail delivery subsystem",
        )
        return bool(email_from) and not any(
            ignored_sender in email_from_lower
            for ignored_sender in ignored_senders
        )

    @api.model
    def _contract_resolve_inbound_author(self, msg_dict, force_create=False):
        if not self._contract_is_external_inbound_email(msg_dict):
            return self.env["res.partner"]

        author_id = msg_dict.get("author_id")
        if author_id:
            author = self.env["res.partner"].sudo().browse(author_id).exists()
            if author:
                return author

        email_from = msg_dict.get("from") or msg_dict.get("email_from")
        partners = self.env["mail.thread"].sudo()._mail_find_partner_from_emails(
            [email_from],
            force_create=force_create,
        )
        return partners[0] if partners else self.env["res.partner"]

    @api.model
    def _contract_create_from_inbound_email(
        self,
        msg_dict,
        source_model=False,
        source_res_id=False,
        name=False,
        description=False,
        partner=False,
        force_create_partner=False,
    ):
        if "helpdesk.ticket" not in self.env or "helpdesk.team" not in self.env:
            return self.browse()
        if not self._contract_is_external_inbound_email(msg_dict):
            return self.browse()

        source_message_id = msg_dict.get("message_id")
        if source_message_id:
            existing_ticket = self.sudo().search([
                ("contract_source_message_id", "=", source_message_id),
            ], limit=1)
            if existing_ticket:
                return existing_ticket

        author_partner = self._contract_resolve_inbound_author(
            msg_dict,
            force_create=force_create_partner,
        )
        if not author_partner:
            return self.browse()
        if author_partner.user_ids:
            return self.browse()

        team = self.env["helpdesk.stage"]._get_customer_care_team()
        if not team:
            return self.browse()

        customer = partner or author_partner.commercial_partner_id or author_partner
        subject = msg_dict.get("subject") or _("Správa od zákazníka")
        ticket_values = {
            "name": name or _("Email od zákazníka: %s") % subject,
            "partner_id": customer.id,
            "team_id": team.id,
            "description": description
            or msg_dict.get("body")
            or _("Prijatá správa od zákazníka."),
            "partner_email": msg_dict.get("email_from") or msg_dict.get("from"),
            "contract_source_message_id": source_message_id,
            "contract_source_model": source_model,
            "contract_source_res_id": source_res_id or 0,
        }
        return self.sudo().create(ticket_values)

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
