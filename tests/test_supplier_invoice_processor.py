# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.tests import common


class TestSupplierInvoiceProcessor(common.TransactionCase):
    def _parse_lets_consult_text(self, invoice_title):
        processor = self.env["supplier.invoice.processor"].new({})
        text = f"""Dodávateľ: {invoice_title}
Let´s Consult, s.r.o.
Hradská 78/B
Dátum vyhotovenia dokl: 28.02.2026
821 07 Bratislava Dátum dodania tovaru/služby 28.02.2026
Dátum splatnosti: 14.03.2026
IČO :35855291 IČ DPH : SK2021728544
DIČ : 2021728544 Odberateľ:
E-mail: info@letsconsult.sk
Var. symbol: 5026017 KS: 0308
Názov Množstvo Cena/jedn Suma DPH %
Fakturujeme Vám za obdobie 02/2026 :
------------------------------------------------------------------------------
Počítačové služby 1 MD = 8 hodín
PC služby 0,50 MD 4.00hod 45.00 EUR 180.00 23.00
------------------------------------------------------------------------------
Základ dane 180.00 EUR
23.00 180.00 EUR 41.40 EUR
DPH celkovo 41.40 EUR
Suma k úhrade 221.40 EUR
"""
        return processor._parse_invoice_data(text, b"")

    def test_lets_consult_extracts_spaced_invoice_number(self):
        data = self._parse_lets_consult_text("F A K T Ú R A Č Í S L O : 5026017")

        self.assertEqual(data["invoice_number"], "5026017")
        self.assertEqual(data["supplier_id"], 1657)
        self.assertEqual(str(data["invoice_date"]), "2026-02-28")
        self.assertEqual(str(data["invoice_due_date"]), "2026-03-14")
        self.assertEqual(data["total_amount"], 221.40)

    def test_lets_consult_extracts_ascii_ocr_invoice_number(self):
        data = self._parse_lets_consult_text("F A K T U R A C I S L O : 5026017")

        self.assertEqual(data["invoice_number"], "5026017")
