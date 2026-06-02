# Copyright 2026 NOVEM IT
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from datetime import datetime, time, timedelta
import logging

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    _INTERNAL_SETTLEMENT_ACCOUNT_CODE = "395000"
    _SYNC_ALERT_RECIPIENTS = "oliver.brunovsky@novem.sk,tomas.juricek@novem.sk"
    _SYNC_ALERT_TZ = "Europe/Bratislava"

    @api.model
    def _get_bank_statement_sync_check_window(self, now_utc=None):
        timezone = pytz.timezone(self._SYNC_ALERT_TZ)
        now_utc = now_utc or fields.Datetime.now()
        if isinstance(now_utc, str):
            now_utc = fields.Datetime.from_string(now_utc)
        if now_utc.tzinfo:
            utc_now = now_utc.astimezone(pytz.utc)
        else:
            utc_now = pytz.utc.localize(now_utc)

        local_now = utc_now.astimezone(timezone)
        local_date_from = local_now.date()
        local_date_to = local_now.date()

        local_start = timezone.localize(datetime.combine(local_date_from, time.min))
        local_end = timezone.localize(
            datetime.combine(local_date_to + timedelta(days=1), time.min)
        )
        return (
            local_start.astimezone(pytz.utc).replace(tzinfo=None),
            local_end.astimezone(pytz.utc).replace(tzinfo=None),
            local_date_from,
            local_date_to,
            local_now,
        )

    @api.model
    def _send_bank_statement_sync_alert(self, date_from, date_to):
        date_from_display = fields.Date.to_string(date_from)
        date_to_display = fields.Date.to_string(date_to)
        email_body = _(
            """
            <p>Pri automatickej kontrole neboli nájdené žiadne nové záznamy bankových výpisov.</p>
            <p><strong>Kontrolované obdobie:</strong> %(date_from)s - %(date_to)s</p>
            <p>Prosím, skontrolujte, či správne funguje synchronizácia bankových výpisov.</p>
            """
        ) % {
            "date_from": date_from_display,
            "date_to": date_to_display,
        }
        mail_values = {
            "subject": _("Upozornenie: chýbajú bankové výpisy"),
            "email_from": self.env.company.email or self.env.user.email,
            "email_to": self._SYNC_ALERT_RECIPIENTS,
            "body_html": email_body,
            "auto_delete": False,
        }
        return (
            self.env["mail.mail"]
            .sudo()
            .with_context(
                mail_notify_force_send=False,
                mail_auto_subscribe_no_notify=True,
                tracking_disable=True,
                mail_create_nolog=True,
            )
            .create(mail_values)
            .send()
        )

    @api.model
    def _check_bank_statement_sync(self, now_utc=None):
        date_start, date_end, local_date_from, local_date_to, local_now = (
            self._get_bank_statement_sync_check_window(now_utc=now_utc)
        )
        if local_now.weekday() >= 5:
            _logger.info(
                "Bank statement sync check skipped for non-workday %s.",
                fields.Date.to_string(local_date_from),
            )
            return False
        if local_now.hour != 10:
            _logger.info(
                "Bank statement sync check skipped outside the 10:00 local hour."
            )
            return False

        statement_line_count = self.sudo().search_count([
            ("create_date", ">=", fields.Datetime.to_string(date_start)),
            ("create_date", "<", fields.Datetime.to_string(date_end)),
        ])
        if statement_line_count:
            _logger.info(
                "Bank statement sync check passed: %s statement line(s) found "
                "between %s and %s.",
                statement_line_count,
                fields.Datetime.to_string(date_start),
                fields.Datetime.to_string(date_end),
            )
            return False

        _logger.warning(
            "Bank statement sync check failed: no statement lines found "
            "between %s and %s.",
            fields.Datetime.to_string(date_start),
            fields.Datetime.to_string(date_end),
        )
        self._send_bank_statement_sync_alert(local_date_from, local_date_to)
        return True

    @api.model
    def cron_check_daily_bank_statement_sync(self):
        return self._check_bank_statement_sync()

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
