# Copyright 2026 NOVEM IT
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    _INTERNAL_SETTLEMENT_ACCOUNT_CODE = "395000"

    def _get_open_clearing_lines(self):
        self.ensure_one()
        _liquidity_lines, suspense_lines, other_lines = self._seek_for_lines()
        candidate_lines = suspense_lines or other_lines.filtered(lambda line: line.account_id.reconcile)
        return candidate_lines.filtered(lambda line: not line.reconciled)

    def _get_internal_settlement_account(self):
        self.ensure_one()
        account = self.env["account.account"].search([
            ("company_ids", "in", self.company_id.id),
            ("code", "=", self._INTERNAL_SETTLEMENT_ACCOUNT_CODE),
        ], limit=1)
        if not account:
            raise UserError(_(
                "Internal settlement account %(code)s was not found for %(company)s."
            ) % {
                "code": self._INTERNAL_SETTLEMENT_ACCOUNT_CODE,
                "company": self.company_id.display_name,
            })
        return account

    @staticmethod
    def _should_use_internal_settlement_account(line):
        return line.account_id.account_type not in ("asset_receivable", "liability_payable")

    def action_pair_opposite_statement_line(self):
        for st_line in self:
            if st_line.is_reconciled:
                continue

            clearing_lines = st_line._get_open_clearing_lines()
            if not clearing_lines:
                raise UserError(_("This transaction has no open clearing line to reconcile."))
            if len(clearing_lines) != 1:
                raise UserError(_("This transaction has multiple open clearing lines. Please reconcile it manually."))

            clearing_line = clearing_lines[0]
            counterpart_lines = self.env["account.move.line"].search([
                ("id", "!=", clearing_line.id),
                ("statement_line_id", "!=", False),
                ("statement_line_id", "!=", st_line.id),
                ("company_id", "=", st_line.company_id.id),
                ("account_id", "=", clearing_line.account_id.id),
                ("reconciled", "=", False),
            ])

            if st_line.partner_id:
                same_partner_lines = counterpart_lines.filtered(
                    lambda line: line.partner_id.commercial_partner_id == st_line.partner_id.commercial_partner_id
                )
                if same_partner_lines:
                    counterpart_lines = same_partner_lines

            exact_counterparts = counterpart_lines.filtered(
                lambda line: (
                    float_compare(
                        abs(line.amount_residual_currency),
                        abs(clearing_line.amount_residual_currency),
                        precision_rounding=line.currency_id.rounding or st_line.currency_id.rounding,
                    ) == 0
                    and float_compare(
                        line.balance,
                        -clearing_line.balance,
                        precision_rounding=st_line.company_id.currency_id.rounding,
                    ) == 0
                )
            )

            if len(exact_counterparts) > 1:
                raise UserError(_(
                    "Multiple opposite statement lines were found on %(account)s for %(amount)s. "
                    "Please reconcile them manually."
                ) % {
                    "account": clearing_line.account_id.display_name,
                    "amount": st_line.currency_id.format(abs(st_line.amount)),
                })
            if not exact_counterparts:
                raise UserError(_(
                    "No opposite open statement line was found on %(account)s for %(amount)s."
                ) % {
                    "account": clearing_line.account_id.display_name,
                    "amount": st_line.currency_id.format(abs(st_line.amount)),
                })

            counterpart_line = exact_counterparts[0]
            lines_to_pair = clearing_line + counterpart_line
            if self._should_use_internal_settlement_account(clearing_line):
                settlement_account = st_line._get_internal_settlement_account()
                lines_to_pair.with_context(check_move_validity=False).write({
                    "account_id": settlement_account.id,
                })
                lines_to_pair = self.env["account.move.line"].browse(lines_to_pair.ids)

            if lines_to_pair.account_id[:1].reconcile:
                lines_to_pair.reconcile()

            st_line.move_id.message_post(
                body=_("Paired with opposite bank transaction %s.") % counterpart_line.move_id.display_name
            )
            counterpart_line.move_id.message_post(
                body=_("Paired with opposite bank transaction %s.") % st_line.move_id.display_name
            )

        if len(self) == 1 and hasattr(self, "action_open_recon_st_line"):
            return self.action_open_recon_st_line()
        return {"type": "ir.actions.act_window_close"}
