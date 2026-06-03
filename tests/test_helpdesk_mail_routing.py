# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestHelpdeskMailRouting(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Stage = cls.env["helpdesk.stage"]
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.MailThread = cls.env["mail.thread"]
        cls.team = cls.Stage._get_customer_care_team()
        if not cls.team:
            cls.team = cls.env["helpdesk.team"].create({
                "name": cls.Stage._CUSTOMER_CARE_TEAM_NAME,
                "member_ids": [(6, 0, [cls.env.ref("base.user_admin").id])],
            })

        cls.partner = cls.partner_a
        cls.partner.write({
            "email": "customer-routing@example.com",
            "customer_rank": 1,
        })
        cls.sale_journal = cls.company_data["default_journal_sale"]
        cls.income_account = cls.company_data["default_account_revenue"]

    def _mail(self, message_id, email_from, subject="Inbound message", body="Body"):
        return "\n".join([
            "From: %s" % email_from,
            "To: catchall@example.com",
            "Subject: %s" % subject,
            "Message-Id: %s" % message_id,
            'Content-Type: text/html; charset="utf-8"',
            "",
            "<p>%s</p>" % body,
        ])

    def _process_reply(self, record, message_id, email_from, subject="Inbound message"):
        return self.MailThread.sudo().message_process(
            record._name,
            self._mail(message_id, email_from, subject=subject),
            thread_id=record.id,
        )

    def _tickets_for_message(self, message_id):
        return self.Ticket.search([
            ("contract_source_message_id", "=", message_id),
        ])

    def _create_invoice(self):
        move = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.partner.id,
            "journal_id": self.sale_journal.id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [
                Command.create({
                    "name": "Service",
                    "quantity": 1,
                    "price_unit": 10,
                    "account_id": self.income_account.id,
                }),
            ],
        })
        move.action_post()
        return move

    def _create_project_task(self):
        project = self.env["project.project"].create({
            "name": "Mail routing project",
            "privacy_visibility": "employees",
        })
        return self.env["project.task"].create({
            "name": "Mail routing task",
            "project_id": project.id,
        })

    def test_invoice_reply_creates_exactly_one_ticket(self):
        invoice = self._create_invoice()
        message_id = "<invoice-routing@example.com>"

        self._process_reply(
            invoice,
            message_id,
            "Customer Routing <customer-routing@example.com>",
            subject="Re: invoice",
        )

        tickets = self._tickets_for_message(message_id)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets.team_id, self.team)
        self.assertEqual(tickets.partner_id, self.partner.commercial_partner_id)
        self.assertEqual(tickets.contract_source_model, "account.move")
        self.assertEqual(tickets.contract_source_res_id, invoice.id)

    def test_invoice_reply_without_author_id_uses_email_from(self):
        invoice = self._create_invoice()
        message_id = "<invoice-no-author@example.com>"

        invoice.message_update({
            "message_type": "email",
            "message_id": message_id,
            "from": "Customer Routing <customer-routing@example.com>",
            "email_from": "Customer Routing <customer-routing@example.com>",
            "subject": "Re: invoice",
            "body": "<p>Question</p>",
        })

        tickets = self._tickets_for_message(message_id)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets.partner_id, self.partner.commercial_partner_id)

    def test_partner_chatter_reply_creates_ticket(self):
        message_id = "<partner-routing@example.com>"

        self._process_reply(
            self.partner,
            message_id,
            "Customer Routing <customer-routing@example.com>",
            subject="Re: partner chatter",
        )

        tickets = self._tickets_for_message(message_id)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets.team_id, self.team)
        self.assertEqual(tickets.partner_id, self.partner.commercial_partner_id)
        self.assertEqual(tickets.contract_source_model, "res.partner")
        self.assertEqual(tickets.contract_source_res_id, self.partner.id)

    def test_generic_chatter_reply_creates_ticket_for_unknown_external_sender(self):
        task = self._create_project_task()
        message_id = "<task-routing@example.com>"

        self._process_reply(
            task,
            message_id,
            "Unknown External <unknown.external@example.com>",
            subject="Re: task",
        )

        tickets = self._tickets_for_message(message_id)
        created_partner = self.env["res.partner"].search([
            ("email_normalized", "=", "unknown.external@example.com"),
        ], limit=1)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets.team_id, self.team)
        self.assertEqual(tickets.partner_id, created_partner)
        self.assertEqual(tickets.contract_source_model, "project.task")
        self.assertEqual(tickets.contract_source_res_id, task.id)

    def test_reply_to_helpdesk_ticket_does_not_create_second_ticket(self):
        source_ticket = self.Ticket.create({
            "name": "Existing ticket",
            "team_id": self.team.id,
            "partner_id": self.partner.id,
        })
        message_id = "<helpdesk-routing@example.com>"

        self._process_reply(
            source_ticket,
            message_id,
            "Customer Routing <customer-routing@example.com>",
            subject="Re: existing ticket",
        )

        self.assertFalse(self._tickets_for_message(message_id))

    def test_internal_user_and_daemon_messages_are_ignored(self):
        task = self._create_project_task()
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Routing Internal User",
            "login": "routing-internal",
            "email": "routing.internal@example.com",
        })
        internal_message_id = "<internal-routing@example.com>"
        daemon_message_id = "<daemon-routing@example.com>"

        self._process_reply(
            task,
            internal_message_id,
            "Routing Internal User <routing.internal@example.com>",
            subject="Internal",
        )
        self._process_reply(
            task,
            daemon_message_id,
            "MAILER-DAEMON <mailer-daemon@example.com>",
            subject="Delivery failure",
        )

        self.assertFalse(self._tickets_for_message(internal_message_id))
        self.assertFalse(self._tickets_for_message(daemon_message_id))
        self.assertTrue(user.partner_id.user_ids)

    def test_processor_3000_forward_is_ignored(self):
        task = self._create_project_task()
        message_id = "<processor-3000-routing@example.com>"

        self._process_reply(
            task,
            message_id,
            "Processor <dodavatelia@novem.sk>",
            subject="FWD to Processor 3000",
        )

        self.assertFalse(self._tickets_for_message(message_id))

    def test_duplicate_message_id_does_not_create_duplicate_ticket(self):
        task = self._create_project_task()
        message_id = "<duplicate-routing@example.com>"
        msg_dict = {
            "message_type": "email",
            "message_id": message_id,
            "from": "Unknown Duplicate <unknown.duplicate@example.com>",
            "email_from": "Unknown Duplicate <unknown.duplicate@example.com>",
            "subject": "Duplicate",
            "body": "<p>Duplicate</p>",
        }

        for _i in range(2):
            self.Ticket._contract_create_from_inbound_email(
                msg_dict,
                source_model=task._name,
                source_res_id=task.id,
                force_create_partner=True,
            )

        self.assertEqual(len(self._tickets_for_message(message_id)), 1)
