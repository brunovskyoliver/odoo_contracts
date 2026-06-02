# Copyright 2026 NOVEM IT
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from datetime import datetime
from unittest.mock import patch

from odoo import fields
from odoo.tests import common


class TestBankStatementSyncAlert(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.BankStatementLine = cls.env["account.bank.statement.line"]
        cls.MailMail = cls.env["mail.mail"]
        cls.env.company.email = "company@example.com"

    def test_weekday_record_found_does_not_send_alert(self):
        now_utc = datetime(2026, 6, 2, 8, 0, 0)

        with patch.object(
            type(self.BankStatementLine), "search_count", return_value=1
        ) as search_count, patch.object(
            type(self.MailMail), "send", return_value=True
        ) as mail_send:
            result = self.BankStatementLine._check_bank_statement_sync(
                now_utc=now_utc
            )

        self.assertFalse(result)
        self.assertFalse(mail_send.called)
        self.assertEqual(search_count.call_args.args[0], [
            ("create_date", ">=", "2026-06-01 22:00:00"),
            ("create_date", "<", "2026-06-02 22:00:00"),
        ])

    def test_weekday_missing_records_sends_alert(self):
        now_utc = datetime(2026, 6, 2, 8, 0, 0)

        with patch.object(
            type(self.BankStatementLine), "search_count", return_value=0
        ), patch.object(type(self.MailMail), "send", return_value=True) as mail_send:
            result = self.BankStatementLine._check_bank_statement_sync(
                now_utc=now_utc
            )

        self.assertTrue(result)
        self.assertEqual(mail_send.call_count, 1)
        mail = self.MailMail.search(
            [("subject", "=", "Upozornenie: chýbajú bankové výpisy")],
            order="id desc",
            limit=1,
        )
        self.assertTrue(mail)
        self.assertEqual(
            mail.email_to,
            "oliver.brunovsky@novem.sk,tomas.juricek@novem.sk",
        )
        self.assertIn(
            "neboli nájdené žiadne nové záznamy bankových výpisov",
            mail.body_html,
        )
        self.assertIn("2026-06-02 - 2026-06-02", mail.body_html)

    def test_weekend_check_is_skipped(self):
        now_utc = datetime(2026, 5, 31, 8, 0, 0)

        with patch.object(
            type(self.BankStatementLine), "search_count", return_value=0
        ) as search_count, patch.object(
            type(self.MailMail), "send", return_value=True
        ) as mail_send:
            result = self.BankStatementLine._check_bank_statement_sync(
                now_utc=now_utc
            )

        self.assertFalse(result)
        self.assertFalse(search_count.called)
        self.assertFalse(mail_send.called)

    def test_workday_check_outside_ten_oclock_is_skipped(self):
        now_utc = datetime(2026, 6, 2, 7, 0, 0)

        with patch.object(
            type(self.BankStatementLine), "search_count", return_value=0
        ) as search_count, patch.object(
            type(self.MailMail), "send", return_value=True
        ) as mail_send:
            result = self.BankStatementLine._check_bank_statement_sync(
                now_utc=now_utc
            )

        self.assertFalse(result)
        self.assertFalse(search_count.called)
        self.assertFalse(mail_send.called)

    def test_cron_xml_is_configured(self):
        cron = self.env.ref("contract.ir_cron_check_bank_statement_sync")

        self.assertEqual(cron.model_id.model, "account.bank.statement.line")
        self.assertEqual(cron.code, "model.cron_check_daily_bank_statement_sync()")
        self.assertEqual(cron.interval_number, 1)
        self.assertEqual(cron.interval_type, "hours")
        self.assertEqual(
            fields.Datetime.to_string(cron.nextcall),
            "2026-06-02 08:00:00",
        )
