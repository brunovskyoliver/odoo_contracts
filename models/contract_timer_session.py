# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ContractTimerSession(models.Model):
    _name = "contract.timer.session"
    _description = "Záznam časovača zamestnanca"
    _order = "start_datetime desc, id desc"

    ACTIVE_STATES = ("running", "paused")

    name = fields.Char(
        compute="_compute_name",
        store=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Používateľ zamestnanca",
        required=True,
        default=lambda self: self.env.user,
        index=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Spoločnosť",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
    )
    start_datetime = fields.Datetime(
        string="Spustené",
        required=True,
        default=fields.Datetime.now,
        readonly=True,
    )
    stop_datetime = fields.Datetime(
        string="Zastavené",
        readonly=True,
    )
    pause_datetime = fields.Datetime(
        string="Pozastavené",
        readonly=True,
    )
    paused_seconds = fields.Integer(
        string="Sekundy pozastavenia",
        readonly=True,
        default=0,
    )
    state = fields.Selection(
        [
            ("running", "Spustené"),
            ("paused", "Pozastavené"),
            ("stopped", "Zastavené"),
            ("discarded", "Vymazané"),
        ],
        string="Stav",
        default="running",
        required=True,
        index=True,
    )
    raw_hours = fields.Float(
        string="Skutočné hodiny",
        digits=(16, 4),
        readonly=True,
    )
    rounded_hours = fields.Float(
        string="Zaokrúhlené hodiny",
        digits=(16, 2),
        readonly=True,
    )
    billable_hours = fields.Float(
        string="Fakturovateľné hodiny",
        digits=(16, 2),
        readonly=True,
    )
    double_rate = fields.Boolean(
        string="2x sadzba",
        readonly=True,
    )
    helpdesk_ticket_id = fields.Many2one(
        comodel_name="helpdesk.ticket",
        string="Helpdesk tiket",
        readonly=True,
        index=True,
    )
    timesheet_line_id = fields.Many2one(
        comodel_name="account.analytic.line",
        string="Vytvorený pracovný výkaz",
        readonly=True,
        copy=False,
    )

    @api.depends("user_id", "start_datetime", "state")
    def _compute_name(self):
        for session in self:
            started_at = session.start_datetime or fields.Datetime.now()
            session.name = _("Časovač používateľa %(user)s od %(start)s") % {
                "user": session.user_id.display_name or _("Neznámy používateľ"),
                "start": fields.Datetime.to_string(started_at),
            }

    def _is_timer_manager(self):
        return self.env.user.has_group("helpdesk.group_helpdesk_manager")

    @api.model_create_multi
    def create(self, vals_list):
        if not self._is_timer_manager():
            for vals in vals_list:
                user_id = vals.get("user_id") or self.env.user.id
                if user_id != self.env.user.id:
                    raise UserError(_("Časovač môžete vytvoriť iba pre seba."))
        return super().create(vals_list)

    def write(self, vals):
        if (
            not self._is_timer_manager()
            and vals.get("user_id")
            and vals["user_id"] != self.env.user.id
        ):
            raise UserError(_("Svoj časovač nemôžete priradiť inému používateľovi."))
        return super().write(vals)

    @api.constrains("user_id", "state")
    def _check_single_running_session(self):
        for session in self.filtered(lambda record: record.state in record.ACTIVE_STATES):
            domain = [
                ("id", "!=", session.id),
                ("user_id", "=", session.user_id.id),
                ("state", "in", self.ACTIVE_STATES),
            ]
            if self.search_count(domain):
                raise ValidationError(_("Každý používateľ môže mať iba jeden aktívny časovač."))

    @api.model
    def _round_hours_up_to_half_hour(self, raw_hours):
        if raw_hours <= 0:
            return 0.0
        return max(math.ceil(raw_hours * 2.0) / 2.0, 0.5)

    @api.model
    def _get_active_session(self):
        return self.search([
            ("user_id", "=", self.env.user.id),
            ("state", "in", self.ACTIVE_STATES),
        ], limit=1)

    @api.model
    def _get_elapsed_seconds(self, session, end_datetime=None):
        if not session or not session.start_datetime:
            return 0
        if session.state == "paused" and session.pause_datetime:
            end_datetime = session.pause_datetime
        end_datetime = end_datetime or fields.Datetime.now()
        elapsed = (
            end_datetime - session.start_datetime
        ).total_seconds() - (session.paused_seconds or 0)
        return max(elapsed, 0)

    @api.model
    def _get_customer_care_ticket_domain(self):
        stage_model = self.env["helpdesk.stage"]
        customer_care_teams = stage_model._get_customer_care_teams()
        return [
            ("active", "=", True),
            ("stage_id.fold", "=", False),
            ("team_id", "in", customer_care_teams.ids),
            ("user_id", "=", self.env.user.id),
        ]

    @api.model
    def _get_valid_timer_ticket(self, ticket_id):
        ticket = self.env["helpdesk.ticket"].browse(ticket_id).exists()
        if not ticket:
            raise UserError(_("Vyberte platný helpdesk tiket."))
        if ticket not in self.env["helpdesk.ticket"].search(
            self._get_customer_care_ticket_domain()
        ):
            raise UserError(
                _("Čas môžete zapisovať iba na svoje otvorené zákaznícke tikety.")
            )
        return ticket

    @api.model
    def _get_ticket_timer_tasks(self, ticket):
        return ticket.fsm_task_ids.filtered(lambda task: task.active and task.is_fsm)

    @api.model
    def _serialize_timer_task(self, task):
        return {
            "id": task.id,
            "display_name": task.display_name,
            "project_name": task.project_id.display_name or "",
            "user_names": ", ".join(task.user_ids.mapped("display_name")),
            "planned_date_begin": (
                fields.Datetime.to_string(task.planned_date_begin)
                if task.planned_date_begin
                else ""
            ),
        }

    @api.model
    def _create_timer_task_from_ticket(self, ticket):
        if not ticket.partner_id and (ticket.partner_name or ticket.partner_email):
            ticket.partner_id = ticket._find_or_create_partner(
                ticket.partner_name,
                ticket.partner_email,
                ticket.company_id.id,
            )
        if not ticket.partner_id:
            raise UserError(
                _("Vybraný tiket musí mať zákazníka, aby bolo možné vytvoriť úlohu.")
            )
        if not ticket.team_id.sudo().fsm_project_id:
            raise UserError(
                _("Tím vybraného tiketu musí mať projekt servisu v teréne, aby bolo možné vytvoriť úlohu.")
            )
        wizard_values = {
            "helpdesk_ticket_id": ticket.id,
            "name": ticket.name,
            "partner_id": ticket.partner_id.id,
            "project_id": ticket.team_id.sudo().fsm_project_id.id,
        }
        wizard = self.env["helpdesk.create.fsm.task"].with_context({
            "use_fsm": True,
            "default_helpdesk_ticket_id": ticket.id,
            "default_user_id": False,
            "default_partner_id": ticket.partner_id.id,
            "default_name": ticket.name,
            "default_project_id": ticket.team_id.sudo().fsm_project_id.id,
        }).create(wizard_values)
        return wizard.action_generate_task()

    @api.model
    def _get_timer_target_task(self, ticket, task_id=False):
        tasks = self._get_ticket_timer_tasks(ticket)
        if task_id:
            task = self.env["project.task"].browse(task_id).exists()
            if not task or task not in tasks:
                raise UserError(_("Vyberte, do ktorej úlohy sa má tento čas uložiť."))
            return task
        if not tasks:
            return self._create_timer_task_from_ticket(ticket)
        if len(tasks) == 1:
            return tasks
        raise UserError(_("Vyberte, do ktorej úlohy sa má tento čas uložiť."))

    @api.model
    def action_get_ticket_timer_tasks(self, ticket_id):
        ticket = self._get_valid_timer_ticket(ticket_id)
        tasks = self._get_ticket_timer_tasks(ticket)
        return {
            "ticket_id": ticket.id,
            "will_create_task": not bool(tasks),
            "requires_task": len(tasks) > 1,
            "task_id": tasks.id if len(tasks) == 1 else False,
            "tasks": [self._serialize_timer_task(task) for task in tasks],
        }

    @api.model
    def _serialize_session(self, session):
        server_datetime = fields.Datetime.now()
        if not session:
            return {
                "running": False,
                "paused": False,
                "state": "idle",
                "session_id": False,
                "start_datetime": False,
                "elapsed_seconds": 0,
                "server_datetime": fields.Datetime.to_string(server_datetime),
            }
        elapsed = self._get_elapsed_seconds(session, server_datetime)
        return {
            "running": session.state == "running",
            "paused": session.state == "paused",
            "state": session.state,
            "session_id": session.id,
            "start_datetime": fields.Datetime.to_string(session.start_datetime),
            "elapsed_seconds": int(elapsed),
            "server_datetime": fields.Datetime.to_string(server_datetime),
        }

    @api.model
    def action_get_timer_status(self):
        return self._serialize_session(self._get_active_session())

    @api.model
    def action_start_timer(self):
        session = self._get_active_session()
        if not session:
            session = self.create({
                "user_id": self.env.user.id,
                "company_id": self.env.company.id,
                "start_datetime": fields.Datetime.now(),
            })
        return self._serialize_session(session)

    @api.model
    def action_pause_timer(self):
        session = self._get_active_session()
        if not session:
            raise UserError(_("Nie je spustený žiadny aktívny časovač na pozastavenie."))
        if session.state == "running":
            session.write({
                "state": "paused",
                "pause_datetime": fields.Datetime.now(),
            })
        return self._serialize_session(session)

    @api.model
    def action_resume_timer(self):
        session = self._get_active_session()
        if not session:
            raise UserError(_("Nie je spustený žiadny aktívny časovač na pokračovanie."))
        if session.state == "paused":
            now = fields.Datetime.now()
            pause_started = session.pause_datetime or now
            paused_seconds = (session.paused_seconds or 0) + max(
                int((now - pause_started).total_seconds()),
                0,
            )
            session.write({
                "state": "running",
                "pause_datetime": False,
                "paused_seconds": paused_seconds,
            })
        return self._serialize_session(session)

    @api.model
    def action_clear_timer(self):
        session = self._get_active_session()
        if not session:
            raise UserError(_("Nie je spustený žiadny aktívny časovač na vymazanie."))
        stop_datetime = fields.Datetime.now()
        raw_hours = self._get_elapsed_seconds(session, stop_datetime) / 3600.0
        session.write({
            "state": "discarded",
            "stop_datetime": stop_datetime,
            "pause_datetime": False,
            "raw_hours": raw_hours,
            "rounded_hours": 0.0,
            "billable_hours": 0.0,
            "double_rate": False,
        })
        return self._serialize_session(False)

    @api.model
    def action_get_open_tickets(self):
        tickets = self.env["helpdesk.ticket"].search(
            self._get_customer_care_ticket_domain(),
            order="priority desc, id desc",
            limit=100,
        )
        return [
            {
                "id": ticket.id,
                "display_name": ticket.display_name,
                "ticket_ref": ticket.ticket_ref or "",
                "partner_name": ticket.partner_id.display_name or "",
            }
            for ticket in tickets
        ]

    @api.model
    def action_stop_timer(self, ticket_id, double_rate=False, task_id=False, description=False):
        session = self._get_active_session()
        if not session:
            raise UserError(_("Nie je spustený žiadny aktívny časovač na uloženie."))
        description = (description or "").strip()
        if not description:
            raise UserError(_("Zadajte popis pre tento záznam času."))

        ticket = self._get_valid_timer_ticket(ticket_id)
        task = self._get_timer_target_task(ticket, task_id=task_id)
        stop_datetime = fields.Datetime.now()
        raw_hours = self._get_elapsed_seconds(session, stop_datetime) / 3600.0
        rounded_hours = self._round_hours_up_to_half_hour(raw_hours)
        billable_hours = rounded_hours * (2 if double_rate else 1)

        timesheet = self.env["account.analytic.line"].create({
            "name": description,
            "date": fields.Date.context_today(self),
            "project_id": task.project_id.id,
            "task_id": task.id,
            "unit_amount": billable_hours,
            "timer_session_id": session.id,
            "timer_raw_hours": raw_hours,
            "timer_rounded_hours": rounded_hours,
            "timer_double_rate": bool(double_rate),
        })
        session.write({
            "state": "stopped",
            "stop_datetime": stop_datetime,
            "pause_datetime": False,
            "raw_hours": raw_hours,
            "rounded_hours": rounded_hours,
            "billable_hours": billable_hours,
            "double_rate": bool(double_rate),
            "helpdesk_ticket_id": ticket.id,
            "timesheet_line_id": timesheet.id,
        })
        ticket.message_post(
            body=_(
                "Časovač zastavil používateľ %(user)s na úlohe %(task)s: %(raw).2f skutočných hodín, %(rounded).2f zaokrúhlených hodín, %(billable).2f fakturovateľných hodín%(double)s."
            ) % {
                "user": self.env.user.display_name,
                "task": task.display_name,
                "raw": raw_hours,
                "rounded": rounded_hours,
                "billable": billable_hours,
                "double": _(" s 2x sadzbou") if double_rate else "",
            },
            message_type="comment",
        )
        return {
            "session_id": session.id,
            "timesheet_line_id": timesheet.id,
            "raw_hours": raw_hours,
            "rounded_hours": rounded_hours,
            "billable_hours": billable_hours,
            "task_id": task.id,
        }
