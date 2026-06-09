# Copyright 2026 NOVEM IT
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError


class TestAccountCustomerSettlement(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Settlement = cls.env["account.customer.settlement"]
        cls.partner = cls.partner_a
        cls.partner.email = "customer@example.com"
        cls.income_account = cls.company_data["default_account_revenue"]
        other_currency_name = "EUR" if cls.env.company.currency_id.name != "EUR" else "USD"
        cls.other_currency = cls.setup_other_currency(other_currency_name)

    def _create_invoice(self, amount, partner=False, posted=True, currency=False):
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": (partner or self.partner).id,
                "currency_id": (currency or self.env.company.currency_id).id,
                "invoice_date": "2026-06-01",
                "invoice_date_due": "2026-06-15",
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Service",
                            "quantity": 1,
                            "price_unit": amount,
                            "account_id": self.income_account.id,
                        }
                    )
                ],
            }
        )
        if posted:
            move.action_post()
        return move

    def _create_refund(self, amount, partner=False, posted=True, currency=False):
        move = self.env["account.move"].create(
            {
                "move_type": "out_refund",
                "partner_id": (partner or self.partner).id,
                "currency_id": (currency or self.env.company.currency_id).id,
                "invoice_date": "2026-06-02",
                "invoice_date_due": "2026-06-16",
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Refund",
                            "quantity": 1,
                            "price_unit": amount,
                            "account_id": self.income_account.id,
                        }
                    )
                ],
            }
        )
        if posted:
            move.action_post()
        return move

    def _fake_pdf_attachment(self, settlement):
        attachment = self.env["ir.attachment"].create(
            {
                "name": "%s.pdf" % settlement.name.replace("/", "_"),
                "datas": base64.b64encode(b"%PDF-1.4\n"),
                "mimetype": "application/pdf",
                "res_model": settlement._name,
                "res_id": settlement.id,
                "type": "binary",
            }
        )
        settlement.pdf_attachment_id = attachment
        return attachment

    def _create_settlement(self, invoices, refunds):
        return self.Settlement.create(
            {
                "partner_id": self.partner.id,
                "company_id": self.env.company.id,
                "currency_id": self.env.company.currency_id.id,
                "invoice_move_ids": [Command.set(invoices.ids)],
                "refund_move_ids": [Command.set(refunds.ids)],
            }
        )

    def _confirm_with_fake_pdf(self, settlement):
        with patch.object(
            type(self.Settlement),
            "_render_pdf_attachment",
            lambda record: self._fake_pdf_attachment(record),
        ):
            return settlement.action_confirm()

    def test_invoice_larger_than_refund_leaves_invoice_residual(self):
        invoice = self._create_invoice(100)
        refund = self._create_refund(65)
        settlement = self._create_settlement(invoice, refund)

        action = self._confirm_with_fake_pdf(settlement)

        self.assertEqual(settlement.state, "confirmed")
        self.assertEqual(invoice.amount_residual, 35)
        self.assertEqual(refund.amount_residual, 0)
        self.assertEqual(settlement.settlement_amount, 65)
        self.assertEqual(settlement.remaining_amount, 35)
        self.assertEqual(settlement.conclusion, "customer_owes_company")
        self.assertEqual(sum(settlement.partial_reconcile_ids.mapped("amount")), 65)
        self.assertEqual(action["res_model"], "mail.compose.message")
        self.assertIn("default_attachment_ids", action["context"])

    def test_refund_larger_than_invoice_leaves_refund_residual(self):
        invoice = self._create_invoice(40)
        refund = self._create_refund(100)
        settlement = self._create_settlement(invoice, refund)

        self._confirm_with_fake_pdf(settlement)

        self.assertEqual(invoice.amount_residual, 0)
        self.assertEqual(refund.amount_residual, 60)
        self.assertEqual(settlement.settlement_amount, 40)
        self.assertEqual(settlement.remaining_amount, 60)
        self.assertEqual(settlement.conclusion, "company_owes_customer")
        self.assertIn("spoločnosť povinná uhradiť zákazníkovi", settlement._get_conclusion_text())

    def test_equal_amounts_fully_reconcile_documents(self):
        invoice = self._create_invoice(80)
        refund = self._create_refund(80)
        settlement = self._create_settlement(invoice, refund)

        self._confirm_with_fake_pdf(settlement)

        self.assertEqual(invoice.amount_residual, 0)
        self.assertEqual(refund.amount_residual, 0)
        self.assertEqual(settlement.remaining_amount, 0)
        self.assertEqual(settlement.conclusion, "settled")

    def test_multiple_documents_allocate_single_settlement(self):
        invoice_1 = self._create_invoice(30)
        invoice_2 = self._create_invoice(70)
        refund = self._create_refund(50)
        settlement = self._create_settlement(invoice_1 | invoice_2, refund)

        self._confirm_with_fake_pdf(settlement)

        self.assertEqual(invoice_1.amount_residual + invoice_2.amount_residual, 50)
        self.assertEqual(refund.amount_residual, 0)
        self.assertEqual(settlement.settlement_amount, 50)
        self.assertEqual(len(settlement.settlement_line_ids), 3)

    def test_validation_requires_invoice_and_refund(self):
        invoice = self._create_invoice(100)
        settlement = self._create_settlement(invoice, self.env["account.move"])

        with self.assertRaises(UserError):
            self._confirm_with_fake_pdf(settlement)

        refund = self._create_refund(50)
        settlement = self._create_settlement(self.env["account.move"], refund)

        with self.assertRaises(UserError):
            self._confirm_with_fake_pdf(settlement)

    def test_validation_rejects_mixed_customer(self):
        other_partner = self.env["res.partner"].create({"name": "Other Customer"})
        invoice = self._create_invoice(100)
        refund = self._create_refund(50, partner=other_partner)
        settlement = self._create_settlement(invoice, refund)

        with self.assertRaises(UserError):
            self._confirm_with_fake_pdf(settlement)

    def test_validation_rejects_mixed_currency(self):
        invoice = self._create_invoice(100)
        refund = self._create_refund(50, currency=self.other_currency)
        settlement = self._create_settlement(invoice, refund)

        with self.assertRaises(UserError):
            self._confirm_with_fake_pdf(settlement)

    def test_validation_rejects_draft_document(self):
        invoice = self._create_invoice(100, posted=False)
        refund = self._create_refund(50)
        settlement = self._create_settlement(invoice, refund)

        with self.assertRaises(UserError):
            self._confirm_with_fake_pdf(settlement)

    def test_validation_rejects_paid_document(self):
        invoice = self._create_invoice(100)
        refund = self._create_refund(100)
        (invoice.line_ids + refund.line_ids).filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        ).reconcile()
        settlement = self._create_settlement(invoice, refund)

        with self.assertRaises(UserError):
            self._confirm_with_fake_pdf(settlement)

    def test_report_html_contains_required_customer_wording(self):
        invoice = self._create_invoice(100)
        refund = self._create_refund(65)
        settlement = self._create_settlement(invoice, refund)
        self._confirm_with_fake_pdf(settlement)

        html = self.env["ir.actions.report"]._render_qweb_html(
            "contract.report_account_customer_settlement_document",
            settlement.ids,
        )[0].decode()

        self.assertIn("VZÁJOMNÝ ZÁPOČET ZÁVÄZKOV A POHĽADÁVOK", html)
        self.assertIn(settlement.name, html)
        self.assertIn(invoice.name, html)
        self.assertIn(refund.name, html)
        self.assertIn(settlement._format_amount(65), html)
        self.assertIn(settlement._format_amount(35), html)
        self.assertIn("Po zápočte zostáva zákazník povinný uhradiť", html)

    def test_account_move_action_creates_editable_draft_settlement(self):
        invoice = self._create_invoice(100)
        refund = self._create_refund(65)

        action = (invoice | refund).action_create_customer_settlement()
        settlement = self.Settlement.browse(action["res_id"])

        self.assertEqual(action["res_model"], "account.customer.settlement")
        self.assertEqual(settlement.state, "draft")
        self.assertEqual(settlement.invoice_move_ids, invoice)
        self.assertEqual(settlement.refund_move_ids, refund)
        self.assertEqual(settlement.partner_id, self.partner.commercial_partner_id)
