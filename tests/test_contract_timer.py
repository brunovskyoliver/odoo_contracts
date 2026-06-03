# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from freezegun import freeze_time

from odoo.exceptions import UserError
from odoo.tests import common, new_test_user


class TestContractTimer(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Timer = cls.env["contract.timer.session"]
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Stage = cls.env["helpdesk.stage"]

        groups = (
            "base.group_user,"
            "helpdesk.group_helpdesk_user,"
            "industry_fsm.group_fsm_user,"
            "hr_timesheet.group_hr_timesheet_user"
        )
        cls.timer_user = new_test_user(
            cls.env,
            login="contract_timer_user",
            groups=groups,
        )
        cls.other_user = new_test_user(
            cls.env,
            login="contract_timer_other_user",
            groups=groups,
        )
        cls.env["hr.employee"].sudo().create({
            "name": "Timer User",
            "user_id": cls.timer_user.id,
            "company_id": cls.env.company.id,
        })
        cls.env["hr.employee"].sudo().create({
            "name": "Other Timer User",
            "user_id": cls.other_user.id,
            "company_id": cls.env.company.id,
        })

        cls.partner = cls.env["res.partner"].create({"name": "Timer Partner"})
        cls.fsm_project = cls.env["project.project"].create({
            "name": "Timer FSM Project",
            "company_id": cls.env.company.id,
            "is_fsm": True,
            "allow_timesheets": True,
        })
        cls.team = cls.env["helpdesk.team"].create({
            "name": cls.Stage._CUSTOMER_CARE_TEAM_NAME,
            "member_ids": [(6, 0, [cls.timer_user.id, cls.other_user.id])],
            "stage_ids": [(6, 0, [])],
            "use_fsm": True,
            "fsm_project_id": cls.fsm_project.id,
        })
        cls.open_stage = cls.Stage.create({
            "name": "Timer Open",
            "sequence": 1,
            "team_ids": [(6, 0, [cls.team.id])],
        })
        cls.closed_stage = cls.Stage.create({
            "name": "Timer Closed",
            "sequence": 99,
            "fold": True,
            "team_ids": [(6, 0, [cls.team.id])],
        })

    def _ticket(self, **values):
        ticket_values = {
            "name": "Timer Ticket",
            "team_id": self.team.id,
            "stage_id": self.open_stage.id,
            "user_id": self.timer_user.id,
            "partner_id": self.partner.id,
        }
        ticket_values.update(values)
        return self.Ticket.create(ticket_values)

    def _task(self, ticket, **values):
        task_values = {
            "name": values.pop("name", "Timer Task"),
            "project_id": self.fsm_project.id,
            "partner_id": ticket.partner_id.id,
            "helpdesk_ticket_id": ticket.id,
        }
        task_values.update(values)
        return self.env["project.task"].create(task_values)

    def test_starting_twice_reuses_running_timer(self):
        Timer = self.Timer.with_user(self.timer_user)

        first_status = Timer.action_start_timer()
        second_status = Timer.action_start_timer()

        self.assertTrue(first_status["running"])
        self.assertEqual(first_status["session_id"], second_status["session_id"])
        self.assertEqual(
            self.Timer.search_count([
                ("user_id", "=", self.timer_user.id),
                ("state", "=", "running"),
            ]),
            1,
        )

    def test_starting_again_reuses_paused_timer(self):
        Timer = self.Timer.with_user(self.timer_user)

        first_status = Timer.action_start_timer()
        Timer.action_pause_timer()
        second_status = Timer.action_start_timer()

        self.assertFalse(second_status["running"])
        self.assertTrue(second_status["paused"])
        self.assertEqual(first_status["session_id"], second_status["session_id"])
        self.assertEqual(
            self.Timer.search_count([
                ("user_id", "=", self.timer_user.id),
                ("state", "in", ("running", "paused")),
            ]),
            1,
        )

    def test_timer_status_includes_server_baseline(self):
        Timer = self.Timer.with_user(self.timer_user)

        with freeze_time("2026-06-02 08:00:00"):
            Timer.action_start_timer()
        with freeze_time("2026-06-02 08:10:00"):
            status = Timer.action_get_timer_status()

        self.assertTrue(status["running"])
        self.assertEqual(status["elapsed_seconds"], 600)
        self.assertEqual(status["server_datetime"], "2026-06-02 08:10:00")

    def test_stop_creates_doubled_timesheet_with_timer_audit(self):
        ticket = self._ticket()
        task = self._task(ticket)
        Timer = self.Timer.with_user(self.timer_user)

        with freeze_time("2026-06-02 08:00:00"):
            status = Timer.action_start_timer()
        with freeze_time("2026-06-02 08:10:00"):
            result = Timer.action_stop_timer(
                ticket.id,
                double_rate=True,
                description="Diagnostika vzdialeneho pristupu",
            )

        session = self.Timer.browse(status["session_id"])
        timesheet = self.env["account.analytic.line"].browse(
            result["timesheet_line_id"]
        )
        self.assertEqual(session.state, "stopped")
        self.assertEqual(session.helpdesk_ticket_id, ticket)
        self.assertEqual(session.timesheet_line_id, timesheet)
        self.assertAlmostEqual(session.raw_hours, 10.0 / 60.0, places=4)
        self.assertEqual(session.rounded_hours, 0.5)
        self.assertEqual(session.billable_hours, 1.0)
        self.assertFalse(timesheet.helpdesk_ticket_id)
        self.assertEqual(timesheet.task_id, task)
        self.assertEqual(timesheet.project_id, task.project_id)
        self.assertEqual(timesheet.timer_session_id, session)
        self.assertAlmostEqual(timesheet.timer_raw_hours, 10.0 / 60.0, places=4)
        self.assertEqual(timesheet.timer_rounded_hours, 0.5)
        self.assertTrue(timesheet.timer_double_rate)
        self.assertEqual(timesheet.unit_amount, 1.0)
        self.assertEqual(timesheet.name, "Diagnostika vzdialeneho pristupu")

    def test_stop_creates_fsm_task_when_ticket_has_none(self):
        ticket = self._ticket()
        Timer = self.Timer.with_user(self.timer_user)

        with freeze_time("2026-06-02 08:00:00"):
            Timer.action_start_timer()
        with freeze_time("2026-06-02 08:10:00"):
            result = Timer.action_stop_timer(ticket.id, description="Servisny zasah")

        timesheet = self.env["account.analytic.line"].browse(
            result["timesheet_line_id"]
        )
        self.assertEqual(len(ticket.fsm_task_ids), 1)
        self.assertEqual(timesheet.task_id, ticket.fsm_task_ids)
        self.assertEqual(timesheet.task_id.name, ticket.name)
        self.assertEqual(timesheet.task_id.partner_id, ticket.partner_id)
        self.assertEqual(timesheet.project_id, self.fsm_project)
        self.assertEqual(ticket.timer_timesheet_count, 1)
        action = ticket.action_view_timer_timesheets()
        self.assertIn(
            timesheet,
            self.env["account.analytic.line"].search(action["domain"]),
        )

    def test_stop_requires_description(self):
        ticket = self._ticket()
        self._task(ticket)
        Timer = self.Timer.with_user(self.timer_user)

        Timer.action_start_timer()

        with self.assertRaises(UserError):
            Timer.action_stop_timer(ticket.id, description=" ")

    def test_stop_requires_task_choice_when_ticket_has_multiple_tasks(self):
        ticket = self._ticket()
        first_task = self._task(ticket, name="First Timer Task")
        second_task = self._task(ticket, name="Second Timer Task")
        Timer = self.Timer.with_user(self.timer_user)

        Timer.action_start_timer()

        with self.assertRaises(UserError):
            Timer.action_stop_timer(ticket.id, description="Servisny zasah")

        result = Timer.action_stop_timer(
            ticket.id,
            task_id=second_task.id,
            description="Servisny zasah",
        )
        timesheet = self.env["account.analytic.line"].browse(
            result["timesheet_line_id"]
        )
        self.assertEqual(timesheet.task_id, second_task)
        self.assertNotEqual(timesheet.task_id, first_task)

    def test_stop_rejects_task_not_belonging_to_ticket(self):
        ticket = self._ticket()
        other_ticket = self._ticket(name="Other Ticket")
        other_task = self._task(other_ticket)
        Timer = self.Timer.with_user(self.timer_user)

        Timer.action_start_timer()

        with self.assertRaises(UserError):
            Timer.action_stop_timer(
                ticket.id,
                task_id=other_task.id,
                description="Servisny zasah",
            )

    def test_ticket_task_target_metadata(self):
        ticket = self._ticket()
        Timer = self.Timer.with_user(self.timer_user)

        no_task_info = Timer.action_get_ticket_timer_tasks(ticket.id)
        self.assertTrue(no_task_info["will_create_task"])
        self.assertFalse(no_task_info["requires_task"])

        first_task = self._task(ticket, name="First Timer Task")
        single_task_info = Timer.action_get_ticket_timer_tasks(ticket.id)
        self.assertFalse(single_task_info["will_create_task"])
        self.assertFalse(single_task_info["requires_task"])
        self.assertEqual(single_task_info["task_id"], first_task.id)

        second_task = self._task(ticket, name="Second Timer Task")
        multiple_task_info = Timer.action_get_ticket_timer_tasks(ticket.id)
        self.assertFalse(multiple_task_info["will_create_task"])
        self.assertTrue(multiple_task_info["requires_task"])
        self.assertEqual(
            {task["id"] for task in multiple_task_info["tasks"]},
            {first_task.id, second_task.id},
        )

    def test_pause_resume_and_save_excludes_paused_time(self):
        ticket = self._ticket()
        Timer = self.Timer.with_user(self.timer_user)

        with freeze_time("2026-06-02 08:00:00"):
            status = Timer.action_start_timer()
        with freeze_time("2026-06-02 08:10:00"):
            paused_status = Timer.action_pause_timer()
        with freeze_time("2026-06-02 08:50:00"):
            still_paused_status = Timer.action_get_timer_status()
            resumed_status = Timer.action_resume_timer()
        with freeze_time("2026-06-02 09:05:00"):
            result = Timer.action_stop_timer(ticket.id, description="Servisny zasah")

        session = self.Timer.browse(status["session_id"])
        timesheet = self.env["account.analytic.line"].browse(
            result["timesheet_line_id"]
        )
        self.assertTrue(paused_status["paused"])
        self.assertEqual(paused_status["elapsed_seconds"], 600)
        self.assertEqual(still_paused_status["elapsed_seconds"], 600)
        self.assertTrue(resumed_status["running"])
        self.assertAlmostEqual(session.raw_hours, 25.0 / 60.0, places=4)
        self.assertEqual(session.rounded_hours, 0.5)
        self.assertEqual(timesheet.unit_amount, 0.5)

    def test_save_from_paused_timer_uses_frozen_elapsed_time(self):
        ticket = self._ticket()
        Timer = self.Timer.with_user(self.timer_user)

        with freeze_time("2026-06-02 08:00:00"):
            status = Timer.action_start_timer()
        with freeze_time("2026-06-02 08:12:00"):
            Timer.action_pause_timer()
        with freeze_time("2026-06-02 10:00:00"):
            result = Timer.action_stop_timer(ticket.id, description="Servisny zasah")

        session = self.Timer.browse(status["session_id"])
        timesheet = self.env["account.analytic.line"].browse(
            result["timesheet_line_id"]
        )
        self.assertEqual(session.state, "stopped")
        self.assertAlmostEqual(session.raw_hours, 12.0 / 60.0, places=4)
        self.assertEqual(timesheet.unit_amount, 0.5)

    def test_clear_paused_timer_without_ticket_discards_without_timesheet(self):
        Timer = self.Timer.with_user(self.timer_user)

        with freeze_time("2026-06-02 08:00:00"):
            status = Timer.action_start_timer()
        with freeze_time("2026-06-02 08:05:00"):
            Timer.action_pause_timer()
        with freeze_time("2026-06-02 09:00:00"):
            cleared_status = Timer.action_clear_timer()

        session = self.Timer.browse(status["session_id"])
        self.assertFalse(cleared_status["running"])
        self.assertFalse(cleared_status["paused"])
        self.assertEqual(session.state, "discarded")
        self.assertAlmostEqual(session.raw_hours, 5.0 / 60.0, places=4)
        self.assertFalse(session.timesheet_line_id)
        self.assertFalse(self.env["account.analytic.line"].search([
            ("timer_session_id", "=", session.id),
        ]))

    def test_rounding_goes_up_to_next_half_hour(self):
        self.assertEqual(self.Timer._round_hours_up_to_half_hour(0), 0.0)
        self.assertEqual(self.Timer._round_hours_up_to_half_hour(0.01), 0.5)
        self.assertEqual(self.Timer._round_hours_up_to_half_hour(0.5), 0.5)
        self.assertEqual(self.Timer._round_hours_up_to_half_hour(0.51), 1.0)
        self.assertEqual(self.Timer._round_hours_up_to_half_hour(1.01), 1.5)

    def test_stop_requires_running_timer(self):
        ticket = self._ticket()

        with self.assertRaises(UserError):
            self.Timer.with_user(self.timer_user).action_stop_timer(
                ticket.id,
                description="Servisny zasah",
            )

    def test_stop_rejects_closed_ticket(self):
        ticket = self._ticket(stage_id=self.closed_stage.id)
        Timer = self.Timer.with_user(self.timer_user)
        Timer.action_start_timer()

        with self.assertRaises(UserError):
            Timer.action_stop_timer(ticket.id, description="Servisny zasah")

    def test_stop_rejects_unassigned_ticket(self):
        ticket = self._ticket(user_id=self.other_user.id)
        Timer = self.Timer.with_user(self.timer_user)
        Timer.action_start_timer()

        with self.assertRaises(UserError):
            Timer.action_stop_timer(ticket.id, description="Servisny zasah")

    def test_users_only_see_their_own_timer_sessions(self):
        status = self.Timer.with_user(self.timer_user).action_start_timer()

        visible_to_owner = self.Timer.with_user(self.timer_user).search([
            ("id", "=", status["session_id"]),
        ])
        visible_to_other = self.Timer.with_user(self.other_user).search([
            ("id", "=", status["session_id"]),
        ])

        self.assertTrue(visible_to_owner)
        self.assertFalse(visible_to_other)
