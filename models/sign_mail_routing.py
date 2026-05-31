# Copyright 2026 Novem
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import ast
import logging

from odoo import api, models
from odoo.tools import email_normalize


_logger = logging.getLogger(__name__)


class MailMail(models.Model):
    _inherit = "mail.mail"

    _SIGN_ROUTE_HEADER = "X-Contract-Sign-Mail-Route"
    _SIGN_RETRY_HEADER = "X-Contract-Sign-Mail-Fallback-Attempted"
    _SIGN_ROUTE_VALUE = "epodpis"

    _PRIMARY_SERVER_PARAM = "contract.sign_mail.primary_server_id"
    _FALLBACK_SERVER_PARAM = "contract.sign_mail.fallback_server_id"
    _PRIMARY_FROM_PARAM = "contract.sign_mail.primary_from"

    _DEFAULT_PRIMARY_SERVER_ID = 5
    _DEFAULT_PRIMARY_SERVER_NAME = "epodpis"
    _DEFAULT_FALLBACK_SERVER_ID = 1
    _DEFAULT_FALLBACK_SERVER_NAME = "novem_elbia"
    _DEFAULT_PRIMARY_FROM = "podpis@smtp.novem.sk"

    @api.model
    def _contract_sign_mail_parse_headers(self, headers, log_malformed=True):
        if not headers:
            return {}
        if isinstance(headers, dict):
            return dict(headers)
        try:
            parsed_headers = ast.literal_eval(headers)
        except (ValueError, SyntaxError, TypeError):
            if log_malformed:
                _logger.warning(
                    "Ignoring malformed outgoing mail headers: %r",
                    headers,
                )
            return {}
        return parsed_headers if isinstance(parsed_headers, dict) else {}

    @api.model
    def _contract_sign_mail_dump_headers(self, headers):
        return repr(headers or {})

    @api.model
    def _contract_sign_mail_get_server(self, param_key, default_id, default_name):
        IrConfig = self.env["ir.config_parameter"].sudo()
        MailServer = self.env["ir.mail_server"].sudo()
        configured_value = (IrConfig.get_param(param_key) or "").strip()
        candidates = [configured_value, str(default_id), default_name]

        for candidate in candidates:
            if not candidate:
                continue
            server = (
                MailServer.browse(int(candidate)).exists()
                if candidate.isdigit()
                else MailServer
            )
            if not server:
                server = MailServer.search([("name", "=", candidate)], limit=1)
            if server and ("active" not in server._fields or server.active):
                return server
        return MailServer

    @api.model
    def _contract_sign_mail_primary_server(self):
        return self._contract_sign_mail_get_server(
            self._PRIMARY_SERVER_PARAM,
            self._DEFAULT_PRIMARY_SERVER_ID,
            self._DEFAULT_PRIMARY_SERVER_NAME,
        )

    @api.model
    def _contract_sign_mail_fallback_server(self):
        return self._contract_sign_mail_get_server(
            self._FALLBACK_SERVER_PARAM,
            self._DEFAULT_FALLBACK_SERVER_ID,
            self._DEFAULT_FALLBACK_SERVER_NAME,
        )

    @api.model
    def _contract_sign_mail_primary_from(self):
        configured_from = (
            self.env["ir.config_parameter"].sudo().get_param(self._PRIMARY_FROM_PARAM)
            or ""
        ).strip()
        return configured_from or self._DEFAULT_PRIMARY_FROM

    @api.model
    def _contract_sign_mail_fallback_from(self, fallback_server):
        if "smtp_from" in fallback_server._fields and fallback_server.smtp_from:
            return fallback_server.smtp_from
        if fallback_server.smtp_user and email_normalize(fallback_server.smtp_user):
            return fallback_server.smtp_user
        return fallback_server.smtp_user or self.env.company.email_formatted

    @api.model
    def _contract_sign_mail_prepare_values(self, mail_values):
        values = dict(mail_values or {})
        primary_server = self._contract_sign_mail_primary_server()
        if primary_server:
            values["mail_server_id"] = primary_server.id
        values["email_from"] = self._contract_sign_mail_primary_from()

        headers = self._contract_sign_mail_parse_headers(values.get("headers"))
        headers[self._SIGN_ROUTE_HEADER] = self._SIGN_ROUTE_VALUE
        headers.pop(self._SIGN_RETRY_HEADER, None)
        values["headers"] = self._contract_sign_mail_dump_headers(headers)
        return values

    def _contract_sign_mail_routed(self):
        return self.exists().filtered(
            lambda mail: mail._contract_sign_mail_parse_headers(
                mail.headers,
                log_malformed=False,
            ).get(mail._SIGN_ROUTE_HEADER)
            == mail._SIGN_ROUTE_VALUE
        )

    def _contract_sign_mail_is_fallback_eligible(self, allow_outgoing=False):
        self.ensure_one()
        headers = self._contract_sign_mail_parse_headers(self.headers)
        if headers.get(self._SIGN_ROUTE_HEADER) != self._SIGN_ROUTE_VALUE:
            return False
        if headers.get(self._SIGN_RETRY_HEADER):
            return False

        primary_server = self._contract_sign_mail_primary_server()
        if not primary_server or self.mail_server_id != primary_server:
            return False
        if self.state != "exception" and not (
            allow_outgoing and self.state == "outgoing"
        ):
            return False
        if self.failure_type in ("mail_email_invalid", "mail_email_missing"):
            return False

        return True

    def _contract_sign_mail_prepare_fallback_retry(self, allow_outgoing=False):
        routed_mails = self._contract_sign_mail_routed()
        if not routed_mails:
            return self.env["mail.mail"]

        fallback_server = self._contract_sign_mail_fallback_server()
        if not fallback_server:
            return self.env["mail.mail"]

        fallback_from = self._contract_sign_mail_fallback_from(fallback_server)
        retry_mails = self.env["mail.mail"]
        for mail in routed_mails:
            if not mail._contract_sign_mail_is_fallback_eligible(
                allow_outgoing=allow_outgoing
            ):
                continue
            headers = mail._contract_sign_mail_parse_headers(mail.headers)
            headers[mail._SIGN_ROUTE_HEADER] = mail._SIGN_ROUTE_VALUE
            headers[mail._SIGN_RETRY_HEADER] = "1"
            mail.write(
                {
                    "mail_server_id": fallback_server.id,
                    "email_from": fallback_from,
                    "reply_to": fallback_from,
                    "headers": mail._contract_sign_mail_dump_headers(headers),
                    "failure_reason": False,
                    "failure_type": False,
                    "state": "outgoing",
                }
            )
            retry_mails |= mail
        return retry_mails

    def send(self, auto_commit=False, raise_exception=False, post_send_callback=None):
        routed_mails = self._contract_sign_mail_routed()
        other_mails = self - routed_mails
        if other_mails:
            super(MailMail, other_mails).send(
                auto_commit=auto_commit,
                raise_exception=raise_exception,
                post_send_callback=post_send_callback,
            )
        if not routed_mails:
            return True

        try:
            result = super(MailMail, routed_mails).send(
                auto_commit=auto_commit,
                raise_exception=raise_exception,
                post_send_callback=post_send_callback,
            )
        except Exception:
            retry_mails = (
                routed_mails.exists()
                .sudo()
                ._contract_sign_mail_prepare_fallback_retry(allow_outgoing=True)
            )
            if not retry_mails:
                raise
            _logger.exception(
                "Primary SMTP send crashed; retrying %s Sign mail(s) through fallback server",
                len(retry_mails),
            )
            super(MailMail, retry_mails).send(
                auto_commit=auto_commit,
                raise_exception=raise_exception,
                post_send_callback=post_send_callback,
            )
            return True

        retry_mails = (
            routed_mails.exists()
            .sudo()
            ._contract_sign_mail_prepare_fallback_retry()
        )
        if retry_mails:
            _logger.warning(
                "Retrying %s Sign mail(s) through fallback SMTP server after primary failure",
                len(retry_mails),
            )
            super(MailMail, retry_mails).send(
                auto_commit=auto_commit,
                raise_exception=raise_exception,
                post_send_callback=post_send_callback,
            )
        return result


class SignRequest(models.Model):
    _inherit = "sign.request"

    @api.model
    def _message_send_mail(
        self,
        body,
        email_layout_xmlid,
        message_values,
        notif_values,
        mail_values,
        force_send=False,
        **kwargs
    ):
        routed_mail_values = self.env["mail.mail"]._contract_sign_mail_prepare_values(
            mail_values
        )
        return super()._message_send_mail(
            body,
            email_layout_xmlid,
            message_values,
            notif_values,
            routed_mail_values,
            force_send=force_send,
            **kwargs
        )
