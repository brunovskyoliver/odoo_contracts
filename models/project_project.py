# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class ProjectProject(models.Model):
    _inherit = "project.project"

    exclude_from_customer_hours = fields.Boolean(
        string="Exclude From Customer Hours",
        help="If enabled, this project is skipped when sending daily customer progress reports.",
        default=False,
    )
    customer_hours_multiplier = fields.Float(
        string="Customer Hours Multiplier",
        default=1.0,
        digits=(16, 2),
        help="Multiplier used in customer worksheet reports. Example: 1.50 means 1 hour is reported as 1.5 hours.",
    )

    @api.constrains("customer_hours_multiplier")
    def _check_customer_hours_multiplier(self):
        for project in self:
            if project.customer_hours_multiplier < 0:
                raise ValidationError(_("Customer Hours Multiplier must be greater than or equal to 0."))

    def action_open_customer_report_wizard(self):
        self.ensure_one()
        return {
            "name": _("Generate Customer Report"),
            "type": "ir.actions.act_window",
            "res_model": "project.customer.report.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_project_id": self.id,
            },
        }

    def _get_daily_customer_report_tasks(self, report_date):
        return self._get_customer_report_tasks(report_date, report_date)

    def _get_customer_report_tasks(self, date_from, date_to):
        self.ensure_one()
        tasks = self.env["project.task"].search([
            ("project_id", "=", self.id),
            ("timesheet_ids.date", ">=", date_from),
            ("timesheet_ids.date", "<=", date_to),
        ])
        # display_satisfied_conditions_count is non-stored, so filter in Python.
        return tasks.filtered(lambda task: task.display_satisfied_conditions_count)

    def _send_daily_customer_report(self, report_date):
        self.ensure_one()
        if self.exclude_from_customer_hours:
            return False
        if not self.partner_id or not self.partner_id.email:
            return False

        tasks = self._get_daily_customer_report_tasks(report_date)
        if not tasks:
            return False

        report_name = "industry_fsm.worksheet_custom"
        report_action = self.env.ref("industry_fsm.task_custom_report", raise_if_not_found=False)
        if not report_action:
            _logger.warning("Missing report action industry_fsm.task_custom_report")
            return False

        # _render_qweb_pdf expects report reference first, then record IDs.
        pdf_content, _report_format = self.env["ir.actions.report"]._render_qweb_pdf(report_name, res_ids=tasks.ids)
        filename = f"{self.name or 'projekt'}-{report_date}-denný-servisný-výkaz.pdf"
        attachment = self.env["ir.attachment"].sudo().create({
            "name": filename,
            "type": "binary",
            "datas": base64.b64encode(pdf_content),
            "mimetype": "application/pdf",
            "res_model": "project.project",
            "res_id": self.id,
        })

        subject = _("Denný servisný výkaz - %(project)s (%(date)s)") % {
            "project": self.name,
            "date": report_date,
        }
        body_html = _(
            """
            <p>Dobrý deň,</p>
            <p>v prílohe Vám posielame denný servisný výkaz pre projekt <strong>%(project)s</strong>.</p>
            <p>Dátum: %(date)s</p>
            """
        ) % {
            "project": self.name,
            "date": report_date,
        }

        mail_values = {
            "subject": subject,
            "body_html": body_html,
            "email_to": self.partner_id.email,
            "author_id": self.env.user.partner_id.id,
            "email_from": self.env.company.email_formatted or self.env.user.email_formatted,
            "attachment_ids": [(4, attachment.id)],
            "auto_delete": True,
        }
        self.env["mail.mail"].sudo().create(mail_values).send()
        return True

    @api.model
    def cron_send_daily_customer_reports(self):
        report_date = fields.Date.context_today(self)
        projects = self.search([
            ("is_fsm", "=", True),
            ("exclude_from_customer_hours", "=", False),
            ("partner_id", "!=", False),
        ])
        for project in projects:
            _logger.info(
                "Attempting to send daily customer FSM report for project %s (%s)",
                project.display_name,
                project.id,
            )
            try:
                project._send_daily_customer_report(report_date)
            except Exception:
                _logger.exception(
                    "Failed to send daily customer FSM report for project %s (%s)",
                    project.display_name,
                    project.id,
                )
