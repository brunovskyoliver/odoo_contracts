# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError


class ProjectCustomerReportWizard(models.TransientModel):
    _name = "project.customer.report.wizard"
    _description = "Project Customer Report Wizard"

    task_id = fields.Many2one(
        comodel_name="project.task",
        string="Task",
        readonly=True,
    )
    project_id = fields.Many2one(
        comodel_name="project.project",
        string="Project",
        required=True,
        readonly=True,
    )
    date_from = fields.Date(
        string="Dátum od",
        required=True,
        default=fields.Date.context_today,
    )
    date_to = fields.Date(
        string="Dátum do",
        required=True,
        default=fields.Date.context_today,
    )

    def action_generate_report(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("The start date must be earlier than or equal to the end date."))

        if self.task_id:
            tasks = self.task_id.filtered(
                lambda task: (
                    task.project_id == self.project_id
                    and task.display_satisfied_conditions_count
                    and bool(task.timesheet_ids.filtered(
                        lambda line: self.date_from <= line.date <= self.date_to
                    ))
                )
            )
        else:
            tasks = self.project_id._get_customer_report_tasks(self.date_from, self.date_to)

        if not tasks:
            raise UserError(_("No tasks with matching timesheets were found for the selected period."))

        report_action = self.env.ref("industry_fsm.task_custom_report", raise_if_not_found=False)
        if not report_action:
            raise UserError(_("The worksheet PDF report action is missing."))

        action = report_action.report_action(
            tasks,
            data={
                "date_from": self.date_from,
                "date_to": self.date_to,
            },
        )
        action["close_on_report_download"] = True
        return action
