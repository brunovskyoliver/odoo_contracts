# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from freezegun import freeze_time

from odoo.exceptions import UserError
from odoo.modules.module import get_module_resource
from odoo.tests import common

from odoo.addons.contract.models import contract_supplier_installment as installment_model
from odoo.addons.contract.wizards import (
    contract_supplier_installment_import_wizard as installment_wizard,
)


class TestContractSupplierInstallment(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "pc3100Plus Test"})
        cls.product = cls.env["product.product"].create(
            {"name": "Prístup do siete internet"}
        )
        cls.purchase_tax = cls.env["account.tax"].create(
            {
                "name": "23% pc3100Plus test",
                "amount": 23.0,
                "type_tax_use": "purchase",
                "company_id": cls.env.company.id,
            }
        )
        cls.expense_account = cls.env["account.account"].search(
            [("code", "=", "518000")],
            limit=1,
        )
        if not cls.expense_account:
            cls.expense_account = cls.env["account.account"].create(
                {
                    "name": "Ostatné služby",
                    "code": "518000",
                    "account_type": "expense",
                }
            )
        cls.contract = cls.env["contract.contract"].create(
            {
                "name": "pc3100Plus supplier contract",
                "partner_id": cls.partner.id,
                "contract_type": "purchase",
                "line_recurrence": True,
            }
        )
        pdf_path = get_module_resource(
            "contract", "NETPRO Vyhne SK 2026 NovemIT.pdf"
        )
        with open(pdf_path, "rb") as pdf_file:
            cls.pdf_data = pdf_file.read()
        cls.pdf_b64 = base64.b64encode(cls.pdf_data)

    def _partner_patch(self):
        return patch.multiple(
            installment_model,
            PC3100PLUS_PARTNER_ID=self.partner.id,
            PC3100PLUS_PRODUCT_ID=self.product.id,
            PC3100PLUS_FALLBACK_TAX_ID=self.purchase_tax.id,
        )

    def _wizard_partner_patch(self):
        return patch.multiple(
            installment_wizard,
            PC3100PLUS_PARTNER_ID=self.partner.id,
            PC3100PLUS_PRODUCT_ID=self.product.id,
        )

    def _new_wizard(self):
        return self.env["contract.supplier.installment.import.wizard"].create(
            {
                "contract_id": self.contract.id,
                "pdf_file": self.pdf_b64,
                "pdf_filename": "NETPRO Vyhne SK 2026 NovemIT.pdf",
            }
        )

    def test_pc3100plus_parser_extracts_pdf_rows(self):
        wizard = self.env["contract.supplier.installment.import.wizard"].new({})
        text = wizard._extract_text_from_pdf(self.pdf_data)
        parsed = wizard._parse_pc3100plus_text(text)

        self.assertEqual(parsed["invoice_number"], "312026077")
        self.assertEqual(len(parsed["lines"]), 12)
        first = parsed["lines"][0]
        last = parsed["lines"][-1]
        self.assertEqual(str(first["delivery_date"]), "2026-01-01")
        self.assertEqual(str(first["due_date"]), "2026-01-16")
        self.assertEqual(first["vat_rate"], 23.0)
        self.assertEqual(first["amount_untaxed"], 292.68)
        self.assertEqual(first["amount_tax"], 67.32)
        self.assertEqual(first["amount_total"], 360.0)
        self.assertEqual(str(last["delivery_date"]), "2026-12-01")
        self.assertEqual(str(last["due_date"]), "2026-12-16")

    def test_wizard_imports_schedule_and_blocks_duplicates(self):
        with self._partner_patch(), self._wizard_partner_patch(), freeze_time("2026-06-02"):
            wizard = self._new_wizard()
            wizard.action_import_schedule()

            lines = self.contract.supplier_installment_line_ids
            self.assertEqual(len(lines), 12)
            self.assertEqual(len(lines.filtered("source_attachment_id")), 12)
            self.assertEqual(
                len(lines.filtered(lambda line: line.state == "invoiced")),
                6,
            )
            self.assertEqual(
                len(lines.filtered(lambda line: line.state == "pending")),
                6,
            )
            info_line = self.contract.contract_line_ids.filtered(
                "is_supplier_installment_info_line"
            )
            self.assertEqual(len(info_line), 1)
            self.assertEqual(info_line.product_id, self.product)
            self.assertEqual(info_line.price_unit, 292.68)
            self.assertEqual(info_line.specific_price, 292.68)
            self.assertEqual(str(info_line.date_start), "2026-01-01")
            self.assertEqual(str(info_line.date_end), "2026-12-31")
            self.assertEqual(str(info_line.last_date_invoiced), "2026-06-30")
            self.assertEqual(str(info_line.recurring_next_date), "2026-07-01")

            chatter_message = self.contract.message_ids.filtered(
                lambda message: "312026077" in (message.body or "")
            )[:1]
            self.assertTrue(chatter_message)
            self.assertIn(
                "NETPRO Vyhne SK 2026 NovemIT.pdf",
                chatter_message.attachment_ids.mapped("name"),
            )

            duplicate_wizard = self._new_wizard()
            with self.assertRaises(UserError):
                duplicate_wizard.action_import_schedule()

    def test_due_installments_create_recurring_vendor_bills(self):
        with self._partner_patch(), self._wizard_partner_patch(), freeze_time("2026-06-02"):
            self._new_wizard().action_import_schedule()

            invoiced_line = self.contract.supplier_installment_line_ids.filtered(
                lambda line: line.delivery_date.month == 6
            )
            invoice = invoiced_line.invoice_id
            self.assertTrue(invoice)
            self.assertEqual(invoice.state, "draft")
            self.assertEqual(invoice.move_type, "in_invoice")
            self.assertEqual(invoice.partner_id, self.partner)
            self.assertEqual(str(invoice.invoice_date), "2026-06-01")
            self.assertEqual(str(invoice.taxable_supply_date), "2026-06-01")
            self.assertEqual(str(invoice.invoice_date_due), "2026-06-16")
            self.assertEqual(invoice.ref, "312026077")
            self.assertEqual(invoice.payment_reference, "312026077")
            self.assertEqual(len(invoice.invoice_line_ids), 1)
            invoice_line = invoice.invoice_line_ids
            self.assertEqual(invoice_line.product_id, self.product)
            self.assertEqual(invoice_line.account_id.code, "518000")
            self.assertEqual(invoice_line.price_unit, 292.68)
            self.assertIn(self.purchase_tax, invoice_line.tax_ids)
            self.assertTrue(
                self.env["ir.attachment"].search(
                    [
                        ("res_model", "=", "account.move"),
                        ("res_id", "=", invoice.id),
                        ("name", "=", "NETPRO Vyhne SK 2026 NovemIT.pdf"),
                    ],
                    limit=1,
                )
            )

    def test_cron_creates_due_pending_installments_once(self):
        with self._partner_patch(), self._wizard_partner_patch(), freeze_time("2026-06-02"):
            self._new_wizard().action_import_schedule()
            self.assertEqual(
                len(self.contract.supplier_installment_line_ids.filtered("invoice_id")),
                6,
            )

        with self._partner_patch(), self._wizard_partner_patch(), freeze_time("2026-12-17"):
            self.env["contract.contract"].cron_recurring_create_invoice()
            self.env["contract.contract"].cron_recurring_create_invoice()
            lines = self.contract.supplier_installment_line_ids
            self.assertEqual(len(lines.filtered("invoice_id")), 8)
            self.assertEqual(
                len(lines.mapped("invoice_id")),
                8,
            )
