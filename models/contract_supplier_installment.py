# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


PC3100PLUS_PARTNER_ID = 1827
PC3100PLUS_PRODUCT_ID = 601
PC3100PLUS_FALLBACK_TAX_ID = 7
PC3100PLUS_EXPENSE_ACCOUNT_CODE = "518000"


class ContractContract(models.Model):
    _inherit = "contract.contract"

    supplier_installment_line_ids = fields.One2many(
        comodel_name="contract.supplier.installment.line",
        inverse_name="contract_id",
        string="Splátkový kalendár dodávateľa",
        copy=False,
    )
    show_supplier_installment_import = fields.Boolean(
        compute="_compute_show_supplier_installment_import",
    )

    @api.depends("contract_type")
    def _compute_show_supplier_installment_import(self):
        for contract in self:
            contract.show_supplier_installment_import = contract.contract_type == "purchase"

    def action_open_supplier_installment_import_wizard(self):
        self.ensure_one()
        if self.contract_type != "purchase":
            raise UserError(_("Splátkový kalendár je dostupný iba pre dodávateľské zmluvy."))
        if self.partner_id.id != PC3100PLUS_PARTNER_ID:
            raise UserError(_("Import splátkového kalendára je podporovaný iba pre pc3100Plus."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Importovať splátkový kalendár"),
            "res_model": "contract.supplier.installment.import.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_contract_id": self.id,
                "active_model": self._name,
                "active_id": self.id,
            },
        }

    def _get_supplier_installment_line_for_invoice_date(self, date_invoice=False):
        self.ensure_one()
        if (
            self.contract_type != "purchase"
            or self.partner_id.id != PC3100PLUS_PARTNER_ID
        ):
            return self.env["contract.supplier.installment.line"]
        target_date = self.recurring_next_date or fields.Date.to_date(date_invoice)
        if not target_date:
            return self.env["contract.supplier.installment.line"]
        return self.supplier_installment_line_ids.filtered(
            lambda line: line.delivery_date == target_date and not line.invoice_id
        )[:1]

    def _uses_supplier_installment_contract_recurrence(self):
        self.ensure_one()
        return bool(
            self.contract_type == "purchase"
            and self.partner_id.id == PC3100PLUS_PARTNER_ID
            and self.contract_line_ids.filtered(
                lambda line: line.is_supplier_installment_info_line
                and not line.is_canceled
            )
        )

    def _prepare_invoice(self, date_invoice, journal=None):
        vals = super()._prepare_invoice(date_invoice, journal=journal)
        installment_line = self._get_supplier_installment_line_for_invoice_date(
            date_invoice
        )
        if installment_line:
            vals.update(
                {
                    "invoice_date": installment_line.delivery_date,
                    "taxable_supply_date": installment_line.delivery_date,
                    "invoice_date_due": installment_line.due_date,
                    "ref": installment_line.invoice_number,
                    "payment_reference": installment_line.invoice_number,
                }
            )
        return vals

    def _recurring_create_invoice(self, date_ref=False):
        supplier_contracts = self.filtered(
            lambda contract: contract._uses_supplier_installment_contract_recurrence()
        )
        regular_contracts = self - supplier_contracts
        moves = self.env["account.move"]
        if regular_contracts:
            moves |= super(
                ContractContract, regular_contracts
            )._recurring_create_invoice(date_ref=date_ref)
        if supplier_contracts:
            moves |= supplier_contracts._recurring_create_supplier_installment_invoice(
                date_ref=date_ref
            )
        return moves

    def _recurring_create_supplier_installment_invoice(self, date_ref=False):
        installment_by_contract = {
            contract.id: contract._get_supplier_installment_line_for_invoice_date(
                date_ref
            )
            for contract in self
        }
        invoices_values = self._prepare_recurring_invoices_values(date_ref)
        if not invoices_values:
            return self.env["account.move"]
        moves = self.env["account.move"].create(invoices_values)
        self._add_contract_origin(moves)
        self._invoice_followers(moves)

        for contract in self:
            contract_moves = moves & contract._get_related_invoices()
            for move in contract_moves:
                contract._copy_mobile_usage_reports_to_invoice(move)

        self._compute_recurring_next_date()
        for contract in self:
            installment_line = installment_by_contract.get(contract.id)
            if not installment_line:
                continue
            invoice = (
                moves
                & contract._get_related_invoices()
            ).filtered(
                lambda move: (
                    move.invoice_date == installment_line.delivery_date
                    and move.ref == installment_line.invoice_number
                )
            )[:1]
            if invoice:
                installment_line.write({"invoice_id": invoice.id, "state": "invoiced"})
                installment_line._copy_source_pdf_to_invoice(invoice)
        return moves


class ContractSupplierInstallmentLine(models.Model):
    _name = "contract.supplier.installment.line"
    _description = "Supplier Contract Installment Line"
    _order = "due_date, id"

    contract_id = fields.Many2one(
        comodel_name="contract.contract",
        string="Zmluva",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="contract_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="contract_id.currency_id",
        readonly=True,
    )
    source_attachment_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="Zdrojové PDF",
        readonly=True,
        ondelete="set null",
    )
    invoice_number = fields.Char(
        string="Číslo faktúry",
        required=True,
        index=True,
    )
    delivery_date = fields.Date(
        string="Dátum dodania",
        required=True,
        index=True,
    )
    due_date = fields.Date(
        string="Dátum splatnosti",
        required=True,
        index=True,
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
    invoice_id = fields.Many2one(
        comodel_name="account.move",
        string="Dodávateľská faktúra",
        readonly=True,
        copy=False,
    )
    state = fields.Selection(
        selection=[
            ("pending", "Čaká"),
            ("invoiced", "Faktúra vytvorená"),
        ],
        string="Stav",
        default="pending",
        required=True,
        readonly=True,
        copy=False,
    )

    _sql_constraints = [
        (
            "invoice_delivery_uniq",
            "unique(contract_id, invoice_number, delivery_date)",
            "Splátka pre túto zmluvu, faktúru a dátum dodania už existuje.",
        )
    ]

    @api.constrains("contract_id")
    def _check_supported_contract(self):
        for line in self:
            if line.contract_id.contract_type != "purchase":
                raise ValidationError(_("Splátkový kalendár je dostupný iba pre dodávateľské zmluvy."))
            if line.contract_id.partner_id.id != PC3100PLUS_PARTNER_ID:
                raise ValidationError(_("Splátkový kalendár je podporovaný iba pre pc3100Plus."))

    @api.constrains("amount_untaxed", "amount_tax", "amount_total")
    def _check_amount_total(self):
        for line in self:
            if abs((line.amount_untaxed + line.amount_tax) - line.amount_total) > 0.01:
                raise ValidationError(_("Súčet základu dane a DPH nesedí s hodnotou na úhradu."))

    def action_create_bill(self):
        invoices = self.env["account.move"]
        for line in self:
            invoices |= line._create_vendor_bill()
        if len(invoices) == 1:
            return {
                "type": "ir.actions.act_window",
                "name": _("Dodávateľská faktúra"),
                "res_model": "account.move",
                "view_mode": "form",
                "res_id": invoices.id,
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Dodávateľské faktúry"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", invoices.ids)],
        }

    def _get_expense_account(self):
        self.ensure_one()
        account = self.env["account.account"].search(
            [
                ("code", "=", PC3100PLUS_EXPENSE_ACCOUNT_CODE),
                ("account_type", "=", "expense"),
                ("deprecated", "=", False),
            ],
            limit=1,
        )
        if not account:
            account = self.env["account.account"].search(
                [
                    ("account_type", "=", "expense"),
                    ("deprecated", "=", False),
                ],
                limit=1,
            )
        if not account:
            raise UserError(_("Nenašlo sa výdavkové konto pre vytvorenie faktúry."))
        return account

    def _get_product(self):
        product = self.env["product.product"].browse(PC3100PLUS_PRODUCT_ID).exists()
        if not product:
            product = self.env["product.product"].search(
                [("name", "ilike", "Prístup do siete internet")],
                limit=1,
            )
        return product

    def _get_purchase_tax(self):
        self.ensure_one()
        tax = self.env["account.tax"].search(
            [
                ("type_tax_use", "=", "purchase"),
                ("amount", "=", self.vat_rate),
                ("company_id", "=", self.company_id.id),
                ("active", "=", True),
            ],
            limit=1,
        )
        if not tax and self.company_id.id == 1:
            tax = self.env["account.tax"].browse(PC3100PLUS_FALLBACK_TAX_ID).exists()
        if not tax:
            tax = self.env["account.tax"].search(
                [
                    ("type_tax_use", "=", "purchase"),
                    ("amount", "=", self.vat_rate),
                    ("active", "=", True),
                ],
                limit=1,
            )
        return tax

    def _copy_source_pdf_to_invoice(self, invoice):
        self.ensure_one()
        if not self.source_attachment_id:
            return
        self.env["ir.attachment"].sudo().create(
            {
                "name": self.source_attachment_id.name,
                "type": "binary",
                "datas": self.source_attachment_id.datas,
                "mimetype": self.source_attachment_id.mimetype or "application/pdf",
                "res_model": "account.move",
                "res_id": invoice.id,
            }
        )

    def _uses_contract_recurrence(self):
        self.ensure_one()
        return bool(
            self.contract_id.contract_line_ids.filtered(
                lambda line: line.is_supplier_installment_info_line
                and not line.is_canceled
            )
        )

    def _create_vendor_bill(self):
        self.ensure_one()
        if self.invoice_id:
            return self.invoice_id
        if self.state != "pending":
            raise UserError(_("Faktúra pre túto splátku už bola vytvorená."))

        product = self._get_product()
        account = self._get_expense_account()
        tax = self._get_purchase_tax()
        line_name = _(
            "Internetové pripojenie podľa splátkového kalendára %(invoice)s (%(date)s)",
            invoice=self.invoice_number,
            date=fields.Date.to_string(self.delivery_date),
        )
        line_vals = {
            "name": line_name,
            "quantity": 1.0,
            "price_unit": self.amount_untaxed,
            "account_id": account.id,
        }
        if product:
            line_vals["product_id"] = product.id
            if product.uom_id:
                line_vals["product_uom_id"] = product.uom_id.id
        if tax:
            line_vals["tax_ids"] = [(6, 0, tax.ids)]

        invoice_vals = {
            "move_type": "in_invoice",
            "partner_id": self.contract_id.partner_id.id,
            "invoice_date": self.delivery_date,
            "invoice_date_due": self.due_date,
            "taxable_supply_date": self.delivery_date,
            "ref": self.invoice_number,
            "payment_reference": self.invoice_number,
            "company_id": self.company_id.id,
            "currency_id": self.currency_id.id,
            "invoice_origin": self.contract_id.name,
            "invoice_line_ids": [(0, 0, line_vals)],
        }
        if self.contract_id.journal_id:
            invoice_vals["journal_id"] = self.contract_id.journal_id.id

        invoice = self.env["account.move"].create(invoice_vals)
        self._copy_source_pdf_to_invoice(invoice)
        self.write({"invoice_id": invoice.id, "state": "invoiced"})
        _logger.info(
            "Created pc3100Plus installment bill %s for contract %s line %s",
            invoice.id,
            self.contract_id.id,
            self.id,
        )
        return invoice

    @api.model
    def cron_create_due_bills(self):
        today = fields.Date.context_today(self)
        due_lines = self.search(
            [
                ("state", "=", "pending"),
                ("invoice_id", "=", False),
                ("due_date", "<=", today),
            ]
        )
        due_lines = due_lines.filtered(lambda line: not line._uses_contract_recurrence())
        for line in due_lines:
            line._create_vendor_bill()
        return True


class ContractLine(models.Model):
    _inherit = "contract.line"

    is_supplier_installment_info_line = fields.Boolean(
        string="Supplier Installment Info Line",
        copy=False,
        help="Technical marker for contract lines created from supplier installment schedules.",
    )

    def _get_supplier_installment_line_for_current_period(self):
        self.ensure_one()
        if not self.is_supplier_installment_info_line:
            return self.env["contract.supplier.installment.line"]
        return self.contract_id._get_supplier_installment_line_for_invoice_date(
            self.recurring_next_date
        )

    def _prepare_invoice_line(self):
        vals = super()._prepare_invoice_line()
        installment_line = self._get_supplier_installment_line_for_current_period()
        if not installment_line:
            return vals

        account = installment_line._get_expense_account()
        tax = installment_line._get_purchase_tax()
        vals.update(
            {
                "name": _(
                    "Internetové pripojenie podľa splátkového kalendára %(invoice)s (%(date)s)",
                    invoice=installment_line.invoice_number,
                    date=fields.Date.to_string(installment_line.delivery_date),
                ),
                "price_unit": installment_line.amount_untaxed,
                "account_id": account.id,
            }
        )
        if tax:
            vals["tax_ids"] = [(6, 0, tax.ids)]
        return vals
