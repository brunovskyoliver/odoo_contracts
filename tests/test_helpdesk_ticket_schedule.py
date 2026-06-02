# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import common


class TestHelpdeskTicketSchedule(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Stage = cls.env["helpdesk.stage"]
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.team = cls.env["helpdesk.team"].create({
            "name": cls.Stage._CUSTOMER_CARE_TEAM_NAME,
            "member_ids": [(6, 0, [cls.env.ref("base.user_admin").id])],
            "stage_ids": [(6, 0, [])],
        })
        cls.new_stage = cls.Stage.create({
            "name": cls.Stage._NEW_STAGE_NAME,
            "sequence": 0,
            "team_ids": [(6, 0, [cls.team.id])],
        })
        cls.resolved_stage = cls.Stage.create({
            "name": cls.Stage._RESOLVED_STAGE_NAME,
            "sequence": 10,
            "fold": True,
            "team_ids": [(6, 0, [cls.team.id])],
        })
        cls.Stage.create_or_update_customer_care_scheduling_stage()
        cls.scheduling_stage = cls.Stage._get_customer_care_stage(
            cls.Stage._SCHEDULING_STAGE_NAME
        )

    def _create_ticket(self, **values):
        ticket_values = {
            "name": "Scheduled ticket",
            "team_id": self.team.id,
            "stage_id": self.scheduling_stage.id,
        }
        ticket_values.update(values)
        return self.Ticket.create(ticket_values)

    def test_stage_setup_creates_scheduling_after_new(self):
        self.assertTrue(self.scheduling_stage)
        self.assertIn(self.team, self.scheduling_stage.team_ids)
        self.assertFalse(self.scheduling_stage.fold)
        self.assertGreater(self.scheduling_stage.sequence, self.new_stage.sequence)
        self.assertEqual(self.team._determine_stage()[self.team.id], self.new_stage)

    def test_stage_setup_adopts_orphan_scheduling_stage(self):
        self.scheduling_stage.write({"team_ids": [(5, 0, 0)]})

        self.Stage.create_or_update_customer_care_scheduling_stage()

        self.assertIn(self.team, self.scheduling_stage.team_ids)
        self.assertEqual(
            len(self.Stage.search([("name", "=", self.Stage._SCHEDULING_STAGE_NAME)])),
            1,
        )

    def test_future_scheduled_ticket_stays_in_scheduling(self):
        ticket = self._create_ticket(
            scheduled_for=fields.Datetime.now() + relativedelta(days=1),
        )
        self.Ticket.cron_publish_scheduled_customer_care_tickets()
        self.assertEqual(ticket.stage_id, self.scheduling_stage)
        self.assertTrue(ticket.show_schedule_fields)

    def test_due_one_time_ticket_moves_to_new_and_clears_schedule(self):
        ticket = self._create_ticket(
            scheduled_for=fields.Datetime.now() - relativedelta(minutes=5),
        )
        self.Ticket.cron_publish_scheduled_customer_care_tickets()
        self.assertEqual(ticket.stage_id, self.new_stage)
        self.assertTrue(ticket.schedule_published)
        self.assertTrue(ticket.show_schedule_fields)
        self.assertFalse(ticket.scheduled_for)

    def test_due_ticket_in_duplicate_scheduling_stage_moves_to_new(self):
        duplicate_scheduling_stage = self.Stage.create({
            "name": self.Stage._SCHEDULING_STAGE_NAME,
            "sequence": 99,
            "team_ids": [(6, 0, self.team.ids)],
        })
        ticket = self._create_ticket(
            stage_id=duplicate_scheduling_stage.id,
            scheduled_for=fields.Datetime.now() - relativedelta(minutes=5),
        )

        self.Ticket.cron_publish_scheduled_customer_care_tickets()

        self.assertFalse(duplicate_scheduling_stage.exists())
        self.assertEqual(ticket.stage_id, self.new_stage)
        self.assertTrue(ticket.schedule_published)
        self.assertFalse(ticket.scheduled_for)

    def test_due_recurring_ticket_moves_to_new_and_keeps_anchor(self):
        scheduled_for = fields.Datetime.now() - relativedelta(days=1)
        ticket = self._create_ticket(
            scheduled_for=scheduled_for,
            schedule_recurring=True,
            schedule_interval=1,
            schedule_interval_type="days",
        )
        self.Ticket.cron_publish_scheduled_customer_care_tickets()
        self.assertEqual(ticket.stage_id, self.new_stage)
        self.assertTrue(ticket.schedule_published)
        self.assertTrue(ticket.show_schedule_fields)
        self.assertEqual(ticket.scheduled_for, scheduled_for)

    def test_recurring_ticket_closed_to_resolved_returns_to_scheduling(self):
        now = fields.Datetime.now()
        scheduled_for = now - relativedelta(months=2)
        expected_next = scheduled_for
        while expected_next <= now:
            expected_next += relativedelta(months=1)

        ticket = self._create_ticket(
            stage_id=self.new_stage.id,
            scheduled_for=scheduled_for,
            schedule_recurring=True,
            schedule_interval=1,
            schedule_interval_type="months",
        )
        ticket.write({"stage_id": self.resolved_stage.id})

        self.assertEqual(ticket.stage_id, self.scheduling_stage)
        self.assertFalse(ticket.schedule_published)
        self.assertTrue(ticket.show_schedule_fields)
        self.assertEqual(ticket.scheduled_for, expected_next)

    def test_non_recurring_ticket_closed_to_resolved_stays_resolved(self):
        ticket = self._create_ticket(stage_id=self.new_stage.id)
        ticket.write({"stage_id": self.resolved_stage.id})
        self.assertEqual(ticket.stage_id, self.resolved_stage)
        self.assertFalse(ticket.show_schedule_fields)

    def test_silent_resolved_redirect_still_maps_to_resolved(self):
        self.Stage.create_or_update_silent_resolved_stages()
        silent_stage = self.Stage.search([
            ("name", "=", self.Stage._SILENT_RESOLVED_STAGE_NAME),
            ("team_ids", "in", self.team.ids),
        ]).filtered(lambda stage: set(stage.team_ids.ids) == {self.team.id})[:1]
        self.assertTrue(silent_stage)

        ticket = self._create_ticket(stage_id=self.new_stage.id)
        ticket.write({"stage_id": silent_stage.id})
        self.assertEqual(ticket.stage_id, self.resolved_stage)

    def test_recurring_schedule_validation(self):
        with self.assertRaises(ValidationError):
            self._create_ticket(schedule_recurring=True)

        with self.assertRaises(ValidationError):
            self._create_ticket(
                scheduled_for=fields.Datetime.now() + relativedelta(days=1),
                schedule_recurring=True,
                schedule_interval=0,
            )
