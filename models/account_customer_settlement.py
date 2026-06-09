# Copyright 2026 NOVEM IT
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class AccountCustomerSettlement(models.Model):
    _name = "account.customer.settlement"
    _description = "Vzájomný zápočet pohľadávok a dobropisov"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Číslo",
        required=True,
        copy=False,
        default=lambda self: _("Nový"),
        tracking=True,
    )
    state = fields.Selection(
        selection=[
            ("draft", "Návrh"),
            ("confirmed", "Potvrdené"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    settlement_date = fields.Date(
        string="Dátum zápočtu",
        default=fields.Date.context_today,
        required=True,
        tracking=True,
    )
    confirmed_date = fields.Datetime(string="Potvrdené dňa", copy=False, readonly=True)
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Zákazník",
        required=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Spoločnosť",
        required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Mena",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    receivable_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Účet pohľadávok",
        readonly=True,
        copy=False,
    )
    invoice_move_ids = fields.Many2many(
        comodel_name="account.move",
        relation="account_customer_settlement_invoice_rel",
        column1="settlement_id",
        column2="move_id",
        string="Vydané faktúry",
        copy=False,
    )
    refund_move_ids = fields.Many2many(
        comodel_name="account.move",
        relation="account_customer_settlement_refund_rel",
        column1="settlement_id",
        column2="move_id",
        string="Dobropisy",
        copy=False,
    )
    move_ids = fields.Many2many(
        comodel_name="account.move",
        string="Doklady",
        compute="_compute_move_ids",
    )
    settlement_line_ids = fields.One2many(
        comodel_name="account.customer.settlement.line",
        inverse_name="settlement_id",
        string="Riadky zápočtu",
        copy=False,
        readonly=True,
    )
    partial_reconcile_ids = fields.Many2many(
        comodel_name="account.partial.reconcile",
        relation="account_customer_settlement_partial_rel",
        column1="settlement_id",
        column2="partial_reconcile_id",
        string="Čiastočné vyrovnania",
        copy=False,
        readonly=True,
    )
    pdf_attachment_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="PDF zápočtu",
        copy=False,
        readonly=True,
    )
    invoice_total = fields.Monetary(
        string="Suma faktúr",
        compute="_compute_amounts",
        currency_field="currency_id",
    )
    refund_total = fields.Monetary(
        string="Suma dobropisov",
        compute="_compute_amounts",
        currency_field="currency_id",
    )
    settlement_amount = fields.Monetary(
        string="Započítaná suma",
        compute="_compute_amounts",
        currency_field="currency_id",
    )
    remaining_amount = fields.Monetary(
        string="Zostatok",
        compute="_compute_amounts",
        currency_field="currency_id",
    )
    conclusion = fields.Selection(
        selection=[
            ("customer_owes_company", "Zákazník dlhuje spoločnosti"),
            ("company_owes_customer", "Spoločnosť dlhuje zákazníkovi"),
            ("settled", "Úplne vyrovnané"),
        ],
        compute="_compute_amounts",
        string="Výsledok",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nový")) == _("Nový"):
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code(
                        "account.customer.settlement"
                    )
                    or _("Nový")
                )
        return super().create(vals_list)

    @api.depends("invoice_move_ids", "refund_move_ids")
    def _compute_move_ids(self):
        for settlement in self:
            settlement.move_ids = settlement.invoice_move_ids | settlement.refund_move_ids

    @api.depends(
        "invoice_move_ids.amount_residual",
        "refund_move_ids.amount_residual",
        "settlement_line_ids.amount_remaining",
        "settlement_line_ids.move_type",
        "state",
    )
    def _compute_amounts(self):
        for settlement in self:
            currency = settlement.currency_id or settlement.company_id.currency_id
            if settlement.state == "confirmed" and settlement.settlement_line_ids:
                invoice_total = sum(
                    line.amount_original
                    for line in settlement.settlement_line_ids
                    if line.move_type == "out_invoice"
                )
                refund_total = sum(
                    abs(line.amount_original)
                    for line in settlement.settlement_line_ids
                    if line.move_type == "out_refund"
                )
                remaining_signed = sum(
                    line.amount_remaining for line in settlement.settlement_line_ids
                )
                settlement_amount = sum(
                    abs(line.amount_settled)
                    for line in settlement.settlement_line_ids
                    if line.move_type == "out_refund"
                )
            else:
                invoice_total = sum(settlement.invoice_move_ids.mapped("amount_residual"))
                refund_total = sum(settlement.refund_move_ids.mapped("amount_residual"))
                settlement_amount = min(invoice_total, refund_total)
                remaining_signed = invoice_total - refund_total

            settlement.invoice_total = currency.round(invoice_total)
            settlement.refund_total = currency.round(refund_total)
            settlement.settlement_amount = currency.round(settlement_amount)
            settlement.remaining_amount = currency.round(abs(remaining_signed))
            if currency.is_zero(remaining_signed):
                settlement.conclusion = "settled"
            elif remaining_signed > 0:
                settlement.conclusion = "customer_owes_company"
            else:
                settlement.conclusion = "company_owes_customer"

    @api.onchange("invoice_move_ids", "refund_move_ids")
    def _onchange_move_ids(self):
        for settlement in self:
            moves = settlement.invoice_move_ids | settlement.refund_move_ids
            if not moves:
                continue
            settlement.partner_id = moves[0].commercial_partner_id
            settlement.company_id = moves[0].company_id
            settlement.currency_id = moves[0].currency_id

    def _get_open_receivable_lines(self, move):
        self.ensure_one()
        currency = move.currency_id
        return move.line_ids.filtered(
            lambda line: (
                line.parent_state == "posted"
                and not line.reconciled
                and line.account_id.account_type == "asset_receivable"
                and not currency.is_zero(abs(line.amount_residual_currency))
            )
        )

    def _validate_documents(self, require_pair=False):
        self.ensure_one()
        invoices = self.invoice_move_ids
        refunds = self.refund_move_ids
        moves = invoices | refunds
        if require_pair and not invoices:
            raise UserError(_("Vyberte aspoň jednu vydanú faktúru."))
        if require_pair and not refunds:
            raise UserError(_("Vyberte aspoň jeden dobropis."))
        if not moves:
            raise UserError(_("Vyberte aspoň jeden odberateľský doklad."))

        bad_types = moves.filtered(
            lambda move: move.move_type not in ("out_invoice", "out_refund")
        )
        if bad_types:
            raise UserError(_("Použiť možno iba vydané faktúry a dobropisy."))
        wrong_bucket = invoices.filtered(lambda move: move.move_type != "out_invoice")
        wrong_bucket |= refunds.filtered(lambda move: move.move_type != "out_refund")
        if wrong_bucket:
            raise UserError(_("Faktúry a dobropisy musia byť vybrané v správnych poliach."))
        not_posted = moves.filtered(lambda move: move.state != "posted")
        if not_posted:
            raise UserError(
                _("Použiť možno iba zaúčtované doklady: %s")
                % ", ".join(not_posted.mapped("display_name"))
            )
        if len(moves.mapped("commercial_partner_id")) != 1:
            raise UserError(_("Všetky doklady musia patriť rovnakému zákazníkovi."))
        if len(moves.mapped("company_id")) != 1:
            raise UserError(_("Všetky doklady musia patriť rovnakej spoločnosti."))
        if len(moves.mapped("currency_id")) != 1:
            raise UserError(_("Všetky doklady musia byť v rovnakej mene."))

        partner = moves[0].commercial_partner_id
        company = moves[0].company_id
        currency = moves[0].currency_id
        open_lines = self.env["account.move.line"]
        for move in moves:
            move_lines = self._get_open_receivable_lines(move)
            if not move_lines:
                raise UserError(
                    _("Doklad %s nemá otvorený zostatok na pohľadávkach.")
                    % move.display_name
                )
            open_lines |= move_lines

        accounts = open_lines.mapped("account_id")
        if len(accounts) != 1:
            raise UserError(_("Všetky doklady musia používať rovnaký účet pohľadávok."))

        self.write(
            {
                "partner_id": partner.id,
                "company_id": company.id,
                "currency_id": currency.id,
                "receivable_account_id": accounts.id,
            }
        )
        return open_lines

    def _get_move_variable_symbol(self, move):
        self.ensure_one()
        return move.payment_reference or move.ref or move.name or ""

    def _prepare_settlement_line_commands(self, before_residuals):
        self.ensure_one()
        commands = [Command.clear()]
        moves = (self.invoice_move_ids | self.refund_move_ids).sorted(
            key=lambda move: (
                move.invoice_date or move.date or fields.Date.today(),
                move.name or "",
                move.id,
            )
        )
        for sequence, move in enumerate(moves, start=1):
            original_abs = before_residuals[move.id]
            remaining_abs = move.amount_residual
            settled_abs = original_abs - remaining_abs
            sign = 1 if move.move_type == "out_invoice" else -1
            commands.append(
                Command.create(
                    {
                        "sequence": sequence,
                        "move_id": move.id,
                        "move_type": move.move_type,
                        "variable_symbol": self._get_move_variable_symbol(move),
                        "document_number": move.name or move.ref or str(move.id),
                        "invoice_date": move.invoice_date or move.date,
                        "date_due": move.invoice_date_due,
                        "amount_original": sign * original_abs,
                        "amount_settled": sign * settled_abs,
                        "amount_remaining": sign * remaining_abs,
                        "currency_id": move.currency_id.id,
                    }
                )
            )
        return commands

    def _render_pdf_attachment(self):
        self.ensure_one()
        report = self.env.ref(
            "contract.action_report_account_customer_settlement",
            raise_if_not_found=False,
        )
        if not report:
            raise UserError(_("PDF zostava zápočtu nebola nájdená."))
        pdf_content, report_format = self.env["ir.actions.report"].sudo()._render_qweb_pdf(
            report,
            [self.id],
        )
        if report_format != "pdf":
            raise UserError(_("Zostava zápočtu sa nevygenerovala ako PDF."))

        filename = "%s.pdf" % (self.name or self.id)
        filename = filename.replace("/", "_")
        attachment = self.env["ir.attachment"].sudo().create(
            {
                "name": filename,
                "datas": base64.b64encode(pdf_content),
                "mimetype": "application/pdf",
                "res_model": self._name,
                "res_id": self.id,
                "type": "binary",
            }
        )
        self.pdf_attachment_id = attachment
        return attachment

    def _get_recipient_emails(self):
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        if partner._fields.get("x_invoice_mails") and partner.x_invoice_mails:
            return partner.x_invoice_mails
        return partner.email or self.partner_id.email or ""

    def _get_mail_server(self):
        return self.env["account.move"]._contract_customer_document_mail_server()

    def _open_email_composer(self):
        self.ensure_one()
        template = self.env.ref(
            "contract.email_template_account_customer_settlement",
            raise_if_not_found=False,
        )
        if not template:
            raise UserError(_("E-mailová šablóna zápočtu nebola nájdená."))
        attachment = self.pdf_attachment_id or self._render_pdf_attachment()
        return {
            "name": _("Odoslať vzájomný zápočet"),
            "type": "ir.actions.act_window",
            "res_model": "mail.compose.message",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_composition_mode": "comment",
                "default_model": self._name,
                "default_res_ids": self.ids,
                "default_template_id": template.id,
                "default_use_template": True,
                "default_attachment_ids": [Command.link(attachment.id)],
                "default_email_layout_xmlid": "mail.mail_notification_light",
                "mark_so_as_sent": False,
            },
        }

    def action_confirm(self):
        for settlement in self:
            if settlement.state != "draft":
                continue
            open_lines = settlement._validate_documents(require_pair=True)
            currency = settlement.currency_id
            before_residuals = {
                move.id: currency.round(move.amount_residual)
                for move in settlement.invoice_move_ids | settlement.refund_move_ids
            }
            before_partials = open_lines.matched_debit_ids | open_lines.matched_credit_ids

            open_lines.reconcile()

            affected_moves = settlement.invoice_move_ids | settlement.refund_move_ids
            affected_moves.invalidate_recordset(["amount_residual", "payment_state"])
            open_lines.invalidate_recordset(
                ["amount_residual", "amount_residual_currency", "reconciled"]
            )
            new_partials = (
                open_lines.matched_debit_ids | open_lines.matched_credit_ids
            ) - before_partials

            if not new_partials:
                raise UserError(_("Nebolo vytvorené žiadne vyrovnanie zápočtu."))

            settlement.write(
                {
                    "state": "confirmed",
                    "confirmed_date": fields.Datetime.now(),
                    "partial_reconcile_ids": [Command.set(new_partials.ids)],
                    "settlement_line_ids": settlement._prepare_settlement_line_commands(
                        before_residuals
                    ),
                }
            )
            attachment = settlement._render_pdf_attachment()
            body = _("Vzájomný zápočet %s bol potvrdený.") % settlement.name
            for move in affected_moves:
                move.message_post(
                    body=body,
                    attachment_ids=[attachment.id],
                )
            settlement.message_post(body=body, attachment_ids=[attachment.id])
        if len(self) == 1:
            return self._open_email_composer()
        return {"type": "ir.actions.act_window_close"}

    def action_download_pdf(self):
        self.ensure_one()
        attachment = self.pdf_attachment_id or self._render_pdf_attachment()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "new",
        }

    def action_open_email_composer(self):
        self.ensure_one()
        if self.state != "confirmed":
            raise UserError(_("Odoslať možno iba potvrdené zápočty."))
        return self._open_email_composer()

    def _format_amount(self, amount):
        self.ensure_one()
        return self.currency_id.format(amount)

    def _format_date(self, date_value):
        return fields.Date.to_string(date_value) if date_value else ""

    def _get_conclusion_text(self):
        self.ensure_one()
        amount = self._format_amount(self.remaining_amount)
        if self.conclusion == "customer_owes_company":
            return _("Po zápočte zostáva zákazník povinný uhradiť spoločnosti %s.") % amount
        if self.conclusion == "company_owes_customer":
            return _("Po zápočte zostáva spoločnosť povinná uhradiť zákazníkovi %s.") % amount
        return _("Po zápočte sú vybrané záväzky a pohľadávky vyrovnané v plnej výške.")


class AccountCustomerSettlementLine(models.Model):
    _name = "account.customer.settlement.line"
    _description = "Riadok snímky vzájomného zápočtu"
    _order = "settlement_id, sequence, id"

    settlement_id = fields.Many2one(
        comodel_name="account.customer.settlement",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Doklad",
        required=True,
        readonly=True,
    )
    move_type = fields.Selection(
        selection=[
            ("out_invoice", "Vydaná faktúra"),
            ("out_refund", "Dobropis"),
        ],
        required=True,
        readonly=True,
    )
    variable_symbol = fields.Char(string="Variabilný symbol", readonly=True)
    document_number = fields.Char(string="Číslo dokladu", readonly=True)
    invoice_date = fields.Date(string="Dátum vystavenia", readonly=True)
    date_due = fields.Date(string="Dátum splatnosti", readonly=True)
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Mena",
        required=True,
        readonly=True,
    )
    amount_original = fields.Monetary(
        string="Suma",
        currency_field="currency_id",
        readonly=True,
    )
    amount_settled = fields.Monetary(
        string="Započítaná suma",
        currency_field="currency_id",
        readonly=True,
    )
    amount_remaining = fields.Monetary(
        string="Zostatok",
        currency_field="currency_id",
        readonly=True,
    )


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_create_customer_settlement(self):
        moves = self.filtered(lambda move: move.move_type in ("out_invoice", "out_refund"))
        if not moves:
            raise UserError(_("Vyberte vydané faktúry alebo dobropisy."))

        invoices = moves.filtered(lambda move: move.move_type == "out_invoice")
        refunds = moves.filtered(lambda move: move.move_type == "out_refund")
        first_move = moves[0]
        settlement = self.env["account.customer.settlement"].create(
            {
                "partner_id": first_move.commercial_partner_id.id,
                "company_id": first_move.company_id.id,
                "currency_id": first_move.currency_id.id,
                "invoice_move_ids": [Command.set(invoices.ids)],
                "refund_move_ids": [Command.set(refunds.ids)],
            }
        )
        settlement._validate_documents(require_pair=False)
        return {
            "name": _("Vzájomný zápočet"),
            "type": "ir.actions.act_window",
            "res_model": "account.customer.settlement",
            "res_id": settlement.id,
            "view_mode": "form",
            "target": "current",
        }
