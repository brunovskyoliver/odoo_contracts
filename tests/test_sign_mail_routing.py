# Copyright 2026 Novem
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.tests import common


class TestSignMailRouting(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.MailMail = cls.env["mail.mail"]
        cls.primary_server = cls.env["ir.mail_server"].create(
            {
                "name": "epodpis",
                "smtp_host": "smtp.resend.com",
                "smtp_port": 587,
                "smtp_user": "resend",
                "smtp_pass": "primary",
                "smtp_encryption": "starttls",
            }
        )
        cls.fallback_server = cls.env["ir.mail_server"].create(
            {
                "name": "novem_elbia",
                "smtp_host": "mail.inetadmin.eu",
                "smtp_port": 587,
                "smtp_user": "ticketing@novem.sk",
                "smtp_pass": "fallback",
                "smtp_encryption": "starttls",
            }
        )
        IrConfig = cls.env["ir.config_parameter"].sudo()
        IrConfig.set_param(
            cls.MailMail._PRIMARY_SERVER_PARAM, str(cls.primary_server.id)
        )
        IrConfig.set_param(
            cls.MailMail._FALLBACK_SERVER_PARAM, str(cls.fallback_server.id)
        )
        IrConfig.set_param(
            cls.MailMail._PRIMARY_FROM_PARAM, "podpis@smtp.novem.sk"
        )

    def _headers(self, mail):
        return self.MailMail._contract_sign_mail_parse_headers(mail.headers)

    def _create_mail(self, headers=None, failure_type="mail_smtp"):
        return self.MailMail.create(
            {
                "body_html": "<p>Test</p>",
                "email_from": "podpis@smtp.novem.sk",
                "email_to": "Recipient <recipient@example.com>",
                "subject": "Test",
                "mail_server_id": self.primary_server.id,
                "headers": self.MailMail._contract_sign_mail_dump_headers(
                    headers or {}
                ),
                "state": "exception",
                "failure_type": failure_type,
                "failure_reason": "Primary failed",
            }
        )

    def test_sign_message_mail_uses_primary_route(self):
        mail = self.env["sign.request"]._message_send_mail(
            "<p>Body</p>",
            "mail.mail_notification_light",
            {"record_name": "Signature request"},
            {
                "model_description": "Signature",
                "company": self.env.company,
            },
            {
                "email_from": self.env.user.email_formatted,
                "author_id": self.env.user.partner_id.id,
                "email_to": "Recipient <recipient@example.com>",
                "subject": "Signature request",
            },
            force_send=False,
        )

        self.assertEqual(mail.mail_server_id, self.primary_server)
        self.assertEqual(mail.email_from, "podpis@smtp.novem.sk")
        self.assertEqual(
            self._headers(mail).get(self.MailMail._SIGN_ROUTE_HEADER),
            self.MailMail._SIGN_ROUTE_VALUE,
        )

    def test_failed_primary_sign_mail_retries_on_fallback(self):
        mail = self._create_mail(
            {self.MailMail._SIGN_ROUTE_HEADER: self.MailMail._SIGN_ROUTE_VALUE}
        )

        retry_mails = mail._contract_sign_mail_prepare_fallback_retry()

        self.assertEqual(retry_mails, mail)
        self.assertEqual(mail.mail_server_id, self.fallback_server)
        self.assertEqual(mail.email_from, "ticketing@novem.sk")
        self.assertEqual(mail.reply_to, "ticketing@novem.sk")
        self.assertEqual(mail.state, "outgoing")
        self.assertEqual(
            self._headers(mail).get(self.MailMail._SIGN_RETRY_HEADER), "1"
        )

    def test_invalid_recipient_failure_is_not_retried(self):
        mail = self._create_mail(
            {self.MailMail._SIGN_ROUTE_HEADER: self.MailMail._SIGN_ROUTE_VALUE},
            failure_type="mail_email_invalid",
        )

        retry_mails = mail._contract_sign_mail_prepare_fallback_retry()

        self.assertFalse(retry_mails)
        self.assertEqual(mail.mail_server_id, self.primary_server)
        self.assertEqual(mail.state, "exception")

    def test_non_sign_mail_is_not_retried(self):
        mail = self._create_mail()

        retry_mails = mail._contract_sign_mail_prepare_fallback_retry()

        self.assertFalse(retry_mails)
        self.assertEqual(mail.mail_server_id, self.primary_server)
        self.assertEqual(mail.state, "exception")

    def test_routed_filter_only_matches_sign_mail(self):
        sign_mail = self._create_mail(
            {self.MailMail._SIGN_ROUTE_HEADER: self.MailMail._SIGN_ROUTE_VALUE}
        )
        helpdesk_like_mail = self._create_mail()

        routed_mails = (sign_mail | helpdesk_like_mail)._contract_sign_mail_routed()

        self.assertEqual(routed_mails, sign_mail)

    def test_deleted_mail_is_not_checked_for_fallback_retry(self):
        mail = self._create_mail(
            {self.MailMail._SIGN_ROUTE_HEADER: self.MailMail._SIGN_ROUTE_VALUE}
        )
        mail_id = mail.id
        mail.unlink()

        retry_mails = (
            self.MailMail.browse(mail_id)
            ._contract_sign_mail_prepare_fallback_retry()
        )

        self.assertFalse(retry_mails)
