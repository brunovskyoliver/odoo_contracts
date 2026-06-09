# Copyright 2026 NOVEM IT
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class TestCustomerDocumentSender(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AccountMove = cls.env["account.move"]
        cls.MailMail = cls.env["mail.mail"]
        cls.MailTemplate = cls.env["mail.template"]
        cls.partner = cls.partner_a
        cls.partner.email = "customer@example.com"
        cls.sale_journal = cls.company_data["default_journal_sale"]
        cls.income_account = cls.company_data["default_account_revenue"]
        cls.mail_server = cls.env["ir.mail_server"].create(
            {
                "name": "fakturacny_smtp",
                "smtp_host": "smtp.example.com",
                "smtp_port": 25,
                "smtp_encryption": "none",
            }
        )

    def _make_pdf_attachment(self, move):
        return self.env["ir.attachment"].create(
            {
                "name": "document.pdf",
                "datas": base64.b64encode(b"%PDF-1.4\n"),
                "mimetype": "application/pdf",
                "res_model": "account.move",
                "res_id": move.id,
            }
        )

    def _make_excel_attachment(self, move):
        return self.env["ir.attachment"].create(
            {
                "name": "usage.xlsx",
                "datas": base64.b64encode(b"excel"),
                "mimetype": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                "res_model": "account.move",
                "res_id": move.id,
            }
        )

    def _create_move(self, move_type="out_invoice", x_invoice_sent=False):
        move = self.AccountMove.create(
            {
                "move_type": move_type,
                "partner_id": self.partner.id,
                "journal_id": self.sale_journal.id,
                "invoice_date": fields.Date.today(),
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Service",
                            "quantity": 1,
                            "price_unit": 10,
                            "account_id": self.income_account.id,
                        }
                    )
                ],
            }
        )
        move.action_post()
        move.x_invoice_sent = x_invoice_sent
        return move

    def _create_template(self):
        return self.MailTemplate.create(
            {
                "name": "Test Customer Document",
                "model_id": self.env.ref("account.model_account_move").id,
                "subject": "{{ object.name }}",
                "email_from": "faktury@novem.sk",
                "email_to": "{{ object.partner_id.email }}",
                "body_html": "<p>Document</p>",
                "auto_delete": True,
            }
        )

    def test_refund_reversal_does_not_copy_x_invoice_sent(self):
        invoice = self._create_move(x_invoice_sent=True)

        refund = invoice._reverse_moves()

        self.assertEqual(refund.move_type, "out_refund")
        self.assertFalse(refund.x_invoice_sent)
        self.assertEqual(refund.contract_customer_mail_state, "pending")

    def test_cron_sends_unsent_invoices_and_refunds_only(self):
        invoice = self._create_move()
        refund = self._create_move(move_type="out_refund")
        self._create_move(x_invoice_sent=True)
        manually_sent = self._create_move()
        manually_sent.is_move_sent = True
        sent_ids = []

        def fake_send(move):
            sent_ids.extend(move.ids)
            move.write(
                {
                    "contract_customer_mail_state": "sent",
                    "x_invoice_sent": True,
                    "is_move_sent": True,
                }
            )
            return True

        with patch.object(
            type(self.AccountMove), "_contract_send_customer_document", fake_send
        ):
            result = self.AccountMove.cron_send_customer_documents(batch_size=10)

        self.assertEqual(result, {"sent": 2, "failed": 0})
        self.assertEqual(set(sent_ids), {invoice.id, refund.id})

    def test_cron_ignores_excluded_company_documents(self):
        invoice = self._create_move()
        sent_ids = []

        def fake_send(move):
            sent_ids.extend(move.ids)
            return True

        with patch.object(
            type(self.AccountMove), "_contract_send_customer_document", fake_send
        ):
            result = (
                self.AccountMove.with_context(
                    contract_customer_document_excluded_company_ids=[
                        invoice.company_id.id
                    ]
                )
                .cron_send_customer_documents(batch_size=10)
            )

        self.assertEqual(result, {"sent": 0, "failed": 0})
        self.assertFalse(sent_ids)
        self.assertEqual(invoice.contract_customer_mail_state, "pending")

    def test_default_customer_document_excluded_company_is_oliver_brunovsky(self):
        self.assertEqual(
            self.AccountMove._contract_customer_document_excluded_company_ids(),
            (5,),
        )

    def test_success_marks_sent_after_smtp_and_keeps_mail_audit(self):
        invoice = self._create_move()
        template = self._create_template()

        def fake_mail_send(mails, *args, **kwargs):
            mails.write({"state": "sent", "failure_reason": False})
            return True

        with patch.object(
            type(self.AccountMove),
            "_contract_customer_document_template",
            return_value=template,
        ), patch.object(
            type(self.AccountMove),
            "_contract_customer_document_pdf_attachment",
            lambda move: self._make_pdf_attachment(move),
        ), patch.object(type(self.MailMail), "send", fake_mail_send):
            result = invoice._contract_send_customer_document()

        self.assertTrue(result)
        self.assertTrue(invoice.x_invoice_sent)
        self.assertEqual(invoice.contract_customer_mail_state, "sent")
        self.assertTrue(invoice.contract_customer_mail_sent_at)
        self.assertTrue(invoice.is_move_sent)
        mail = invoice.contract_customer_mail_id
        self.assertTrue(mail)
        self.assertFalse(mail.auto_delete)
        self.assertEqual(mail.mail_server_id, self.mail_server)
        self.assertEqual(mail.attachment_ids.mapped("mimetype"), ["application/pdf"])
        self.assertNotIn("X-Contract-Sign-Mail-Route", mail.headers or "")

    def test_success_attaches_existing_invoice_attachments(self):
        invoice = self._create_move()
        template = self._create_template()
        excel_attachment = self._make_excel_attachment(invoice)

        def fake_mail_send(mails, *args, **kwargs):
            mails.write({"state": "sent", "failure_reason": False})
            return True

        with patch.object(
            type(self.AccountMove),
            "_contract_customer_document_template",
            return_value=template,
        ), patch.object(
            type(self.AccountMove),
            "_contract_customer_document_pdf_attachment",
            lambda move: self._make_pdf_attachment(move),
        ), patch.object(type(self.MailMail), "send", fake_mail_send):
            result = invoice._contract_send_customer_document()

        self.assertTrue(result)
        mail = invoice.contract_customer_mail_id
        self.assertIn(excel_attachment, mail.attachment_ids)
        self.assertEqual(len(mail.attachment_ids), 2)
        self.assertEqual(
            set(mail.attachment_ids.mapped("mimetype")),
            {
                "application/pdf",
                (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
            },
        )

    def test_smtp_failure_keeps_document_unsent_for_retry(self):
        invoice = self._create_move()
        template = self._create_template()

        def fake_mail_send(mails, *args, **kwargs):
            mails.write(
                {
                    "state": "exception",
                    "failure_type": "mail_smtp",
                    "failure_reason": "SMTP down",
                }
            )
            return True

        with patch.object(
            type(self.AccountMove),
            "_contract_customer_document_template",
            return_value=template,
        ), patch.object(
            type(self.AccountMove),
            "_contract_customer_document_pdf_attachment",
            lambda move: self._make_pdf_attachment(move),
        ), patch.object(type(self.MailMail), "send", fake_mail_send):
            result = invoice._contract_send_customer_document()

        self.assertFalse(result)
        self.assertFalse(invoice.x_invoice_sent)
        self.assertEqual(invoice.contract_customer_mail_state, "failed")
        self.assertEqual(invoice.contract_customer_mail_failure_reason, "SMTP down")

    def test_cron_xml_is_configured(self):
        cron = self.env.ref("contract.ir_cron_send_customer_documents")

        self.assertEqual(cron.model_id.model, "account.move")
        self.assertEqual(
            cron.code,
            "model.with_context(contract_customer_document_excluded_company_ids=[5]).cron_send_customer_documents(batch_size=20)",
        )
        self.assertEqual(cron.interval_number, 20)
        self.assertEqual(cron.interval_type, "minutes")
