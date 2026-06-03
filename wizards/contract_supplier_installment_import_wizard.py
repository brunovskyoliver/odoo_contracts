# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import io
import logging
import re

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.contract.models.contract_supplier_installment import (
    PC3100PLUS_PARTNER_ID,
    PC3100PLUS_PRODUCT_ID,
)

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

_logger = logging.getLogger(__name__)


class ContractSupplierInstallmentImportWizard(models.TransientModel):
    _name = "contract.supplier.installment.import.wizard"
    _description = "Import Supplier Installment Schedule"

    contract_id = fields.Many2one(
        comodel_name="contract.contract",
        string="Zmluva",
        required=True,
    )
    pdf_file = fields.Binary(
        string="PDF splátkového kalendára",
        required=True,
    )
    pdf_filename = fields.Char(
        string="Názov súboru",
    )
    invoice_number = fields.Char(
        string="Číslo faktúry",
        readonly=True,
    )
    valid_from = fields.Date(
        string="Platí od",
        readonly=True,
    )
    valid_to = fields.Date(
        string="Platí do",
        readonly=True,
    )
    line_ids = fields.One2many(
        comodel_name="contract.supplier.installment.import.line",
        inverse_name="wizard_id",
        string="Splátky",
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="contract_id.currency_id",
        readonly=True,
    )

    @api.onchange("pdf_file", "pdf_filename")
    def _onchange_pdf_file(self):
        for wizard in self:
            wizard.invoice_number = False
            wizard.valid_from = False
            wizard.valid_to = False
            wizard.line_ids = [(5, 0, 0)]
            if wizard.pdf_file:
                try:
                    wizard._parse_pdf_to_preview()
                except UserError:
                    raise
                except Exception as exc:
                    raise UserError(_("PDF sa nepodarilo spracovať: %s") % exc) from exc

    def action_parse_pdf(self):
        self.ensure_one()
        self._parse_pdf_to_preview()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _check_contract(self):
        self.ensure_one()
        if self.contract_id.contract_type != "purchase":
            raise UserError(_("Splátkový kalendár je dostupný iba pre dodávateľské zmluvy."))
        if self.contract_id.partner_id.id != PC3100PLUS_PARTNER_ID:
            raise UserError(_("Import splátkového kalendára je podporovaný iba pre pc3100Plus."))

    def _parse_pdf_to_preview(self):
        self.ensure_one()
        self._check_contract()
        if not self.pdf_file:
            raise UserError(_("Nahrajte PDF splátkového kalendára."))
        pdf_data = base64.b64decode(self.pdf_file)
        text = self._extract_text_from_pdf(pdf_data)
        parsed = self._parse_pc3100plus_text(text)
        self._check_duplicate_invoice(parsed["invoice_number"])
        self.update(
            {
                "invoice_number": parsed["invoice_number"],
                "valid_from": parsed.get("valid_from"),
                "valid_to": parsed.get("valid_to"),
                "line_ids": [(5, 0, 0)]
                + [
                    (
                        0,
                        0,
                        {
                            "delivery_date": line["delivery_date"],
                            "due_date": line["due_date"],
                            "vat_rate": line["vat_rate"],
                            "amount_untaxed": line["amount_untaxed"],
                            "amount_tax": line["amount_tax"],
                            "amount_total": line["amount_total"],
                        },
                    )
                    for line in parsed["lines"]
                ],
            }
        )
        return parsed

    def _check_duplicate_invoice(self, invoice_number):
        existing = self.env["contract.supplier.installment.line"].search_count(
            [
                ("contract_id", "=", self.contract_id.id),
                ("invoice_number", "=", invoice_number),
            ]
        )
        if existing:
            raise UserError(
                _("Splátkový kalendár pre faktúru %s už je na tejto zmluve importovaný.")
                % invoice_number
            )

    @api.model
    def _extract_text_from_pdf(self, pdf_data):
        if not pdf_data:
            raise UserError(_("PDF neobsahuje žiadne dáta."))
        text = ""
        if pdfplumber:
            try:
                with pdfplumber.open(io.BytesIO(pdf_data)) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n"
                if text.strip():
                    return text
            except Exception as exc:
                _logger.warning("pc3100Plus pdfplumber extraction failed: %s", exc)
        if PyPDF2:
            try:
                reader = PyPDF2.PdfReader(io.BytesIO(pdf_data))
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                if text.strip():
                    return text
            except Exception as exc:
                _logger.warning("pc3100Plus PyPDF2 extraction failed: %s", exc)
        raise UserError(
            _(
                "PDF text extraction failed. The document may be image-based or PDF libraries are missing."
            )
        )

    @api.model
    def _parse_pc3100plus_text(self, text):
        if not text:
            raise UserError(_("Z PDF sa nepodarilo extrahovať text."))
        text_lower = text.lower()
        required_markers = ["pc3100plus", "splátkový kalendár"]
        if not all(marker in text_lower for marker in required_markers):
            raise UserError(_("PDF nevyzerá ako pc3100Plus splátkový kalendár."))

        invoice_match = re.search(
            r"Fakt[úu]ra\s+č\.\s*([A-Z0-9][A-Z0-9/-]*)",
            text,
            re.IGNORECASE,
        )
        if not invoice_match:
            raise UserError(_("V PDF sa nenašlo číslo faktúry."))
        invoice_number = invoice_match.group(1).strip()

        valid_from = valid_to = False
        validity_match = re.search(
            r"plat[íi]\s+od\s+(\d{1,2}\.\d{1,2}\.\d{4})\s+do\s+(\d{1,2}\.\d{1,2}\.\d{4})",
            text,
            re.IGNORECASE,
        )
        if validity_match:
            valid_from = self._parse_date(validity_match.group(1))
            valid_to = self._parse_date(validity_match.group(2))

        row_pattern = re.compile(
            r"(?P<delivery>\d{1,2}\.\d{1,2}\.\d{4})\s+"
            r"(?P<due>\d{1,2}\.\d{1,2}\.\d{4})\s+"
            r"(?P<vat>\d+(?:[,.]\d+)?)%\s+"
            r"(?P<untaxed>\d[\d\s]*[,.]\d{2})\s+"
            r"(?P<tax>\d[\d\s]*[,.]\d{2})\s+"
            r"(?P<total>\d[\d\s]*[,.]\d{2})"
        )
        lines = []
        for match in row_pattern.finditer(text):
            amount_untaxed = self._parse_amount(match.group("untaxed"))
            amount_tax = self._parse_amount(match.group("tax"))
            amount_total = self._parse_amount(match.group("total"))
            if abs((amount_untaxed + amount_tax) - amount_total) > 0.01:
                raise UserError(
                    _(
                        "Riadok %(date)s má nesprávny súčet: %(base)s + %(tax)s != %(total)s",
                        date=match.group("delivery"),
                        base=amount_untaxed,
                        tax=amount_tax,
                        total=amount_total,
                    )
                )
            lines.append(
                {
                    "delivery_date": self._parse_date(match.group("delivery")),
                    "due_date": self._parse_date(match.group("due")),
                    "vat_rate": self._parse_amount(match.group("vat")),
                    "amount_untaxed": amount_untaxed,
                    "amount_tax": amount_tax,
                    "amount_total": amount_total,
                }
            )
        if not lines:
            raise UserError(_("V PDF sa nenašli žiadne splátky."))
        return {
            "invoice_number": invoice_number,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "lines": lines,
        }

    @api.model
    def _parse_date(self, date_text):
        day, month, year = [int(part) for part in date_text.split(".")]
        return fields.Date.to_date("%04d-%02d-%02d" % (year, month, day))

    @api.model
    def _parse_amount(self, amount_text):
        return float(amount_text.replace(" ", "").replace(",", "."))

    def _get_installment_product(self):
        product = self.env["product.product"].browse(PC3100PLUS_PRODUCT_ID).exists()
        if not product:
            product = self.env["product.product"].search(
                [("name", "ilike", "Prístup do siete internet")],
                limit=1,
            )
        return product

    def _create_or_update_contract_info_line(self, installment_lines):
        self.ensure_one()
        if not installment_lines:
            return self.env["contract.line"]

        first_line = installment_lines.sorted("delivery_date")[0]
        last_line = installment_lines.sorted("delivery_date")[-1]
        schedule_end = self.valid_to or (
            last_line.delivery_date + relativedelta(months=1, days=-1)
        )
        product = self._get_installment_product()
        monthly_untaxed = "%.2f" % first_line.amount_untaxed
        monthly_total = "%.2f" % first_line.amount_total
        line_name = _(
            "Prístup do siete internet podľa splátkového kalendára %(invoice)s "
            "(mesačne %(amount)s bez DPH / %(total)s s DPH)",
            invoice=self.invoice_number,
            amount=monthly_untaxed,
            total=monthly_total,
        )
        vals = {
            "contract_id": self.contract_id.id,
            "product_id": product.id if product else False,
            "name": line_name,
            "quantity": 1.0,
            "price_unit": first_line.amount_untaxed,
            "specific_price": first_line.amount_untaxed,
            "recurring_rule_type": "monthly",
            "recurring_interval": 1,
            "recurring_invoicing_type": "pre-paid",
            "date_start": first_line.delivery_date,
            "date_end": schedule_end,
            "recurring_next_date": first_line.delivery_date,
            "is_supplier_installment_info_line": True,
        }
        if product and product.uom_id:
            vals["uom_id"] = product.uom_id.id

        contract_line = self.env["contract.line"].create(vals)
        return contract_line

    def _create_due_contract_invoices(self, contract_line):
        today = fields.Date.context_today(self)
        invoices = self.env["account.move"]
        while (
            contract_line.recurring_next_date
            and contract_line.recurring_next_date <= today
        ):
            previous_next_date = contract_line.recurring_next_date
            invoices |= self.contract_id._recurring_create_invoice()
            contract_line.invalidate_recordset(
                ["last_date_invoiced", "recurring_next_date"]
            )
            if contract_line.recurring_next_date == previous_next_date:
                raise UserError(
                    _(
                        "Nepodarilo sa posunúť dátum najbližšej fakturácie pre riadok %s."
                    )
                    % contract_line.display_name
                )
        return invoices

    def action_import_schedule(self):
        self.ensure_one()
        self._check_contract()
        if not self.line_ids:
            self._parse_pdf_to_preview()
        self._check_duplicate_invoice(self.invoice_number)
        attachment = self.env["ir.attachment"].sudo().create(
            {
                "name": self.pdf_filename or _("pc3100Plus splátkový kalendár.pdf"),
                "type": "binary",
                "datas": self.pdf_file,
                "mimetype": "application/pdf",
                "res_model": "contract.contract",
                "res_id": self.contract_id.id,
            }
        )
        installment_lines = self.env["contract.supplier.installment.line"]
        for preview_line in self.line_ids:
            installment_lines |= installment_lines.create(
                {
                    "contract_id": self.contract_id.id,
                    "source_attachment_id": attachment.id,
                    "invoice_number": self.invoice_number,
                    "delivery_date": preview_line.delivery_date,
                    "due_date": preview_line.due_date,
                    "vat_rate": preview_line.vat_rate,
                    "amount_untaxed": preview_line.amount_untaxed,
                    "amount_tax": preview_line.amount_tax,
                    "amount_total": preview_line.amount_total,
                }
            )
        contract_line = self._create_or_update_contract_info_line(installment_lines)
        self.contract_id._compute_recurring_next_date()
        invoices = self._create_due_contract_invoices(contract_line)
        self.contract_id.message_post(
            body=_(
                "Importovaný splátkový kalendár %(invoice)s: %(count)s splátok. "
                "Vytvorený opakovaný riadok zmluvy: %(line)s. "
                "Vytvorené faktúry: %(invoice_count)s.",
                invoice=self.invoice_number,
                count=len(installment_lines),
                line=contract_line.display_name,
                invoice_count=len(invoices),
            ),
            attachment_ids=[attachment.id],
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Splátkový kalendár"),
            "res_model": "contract.contract",
            "res_id": self.contract_id.id,
            "view_mode": "form",
        }


class ContractSupplierInstallmentImportLine(models.TransientModel):
    _name = "contract.supplier.installment.import.line"
    _description = "Supplier Installment Import Preview Line"
    _order = "due_date, id"

    wizard_id = fields.Many2one(
        comodel_name="contract.supplier.installment.import.wizard",
        required=True,
        ondelete="cascade",
    )
    currency_id = fields.Many2one(
        related="wizard_id.currency_id",
        readonly=True,
    )
    delivery_date = fields.Date(
        string="Dátum dodania",
        required=True,
    )
    due_date = fields.Date(
        string="Dátum splatnosti",
        required=True,
    )
    vat_rate = fields.Float(
        string="Sadzba DPH",
        required=True,
    )
    amount_untaxed = fields.Monetary(
        string="Základ dane",
        currency_field="currency_id",
        required=True,
    )
    amount_tax = fields.Monetary(
        string="Výška dane",
        currency_field="currency_id",
        required=True,
    )
    amount_total = fields.Monetary(
        string="Na úhradu",
        currency_field="currency_id",
        required=True,
    )
