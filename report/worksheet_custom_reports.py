# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class TaskCustomReport(models.AbstractModel):
    _inherit = "report.industry_fsm.worksheet_custom"

    @api.model
    def _get_report_values(self, docids, data=None):
        report_values = super()._get_report_values(docids, data)
        timesheet_lines_map = {}
        multiplied_timesheet_hours = {}
        multiplied_total_hours = {}
        data = data or {}
        date_from = fields.Date.to_date(data.get("date_from")) if data.get("date_from") else False
        date_to = fields.Date.to_date(data.get("date_to")) if data.get("date_to") else False

        for task in report_values.get("docs"):
            multiplier = task.project_id.customer_hours_multiplier or 1.0
            timesheet_lines = task.timesheet_ids
            if date_from:
                timesheet_lines = timesheet_lines.filtered(lambda line: line.date >= date_from)
            if date_to:
                timesheet_lines = timesheet_lines.filtered(lambda line: line.date <= date_to)

            if task.project_id.exclude_from_customer_hours:
                timesheet_lines_map[task.id] = task.timesheet_ids.browse()
                multiplied_total_hours[task.id] = 0.0
                continue

            timesheet_lines_map[task.id] = timesheet_lines
            for line in timesheet_lines:
                multiplied_timesheet_hours[line.id] = line.unit_amount * multiplier
            multiplied_total_hours[task.id] = sum(timesheet_lines.mapped("unit_amount")) * multiplier

        report_values.update({
            "timesheet_lines_map": timesheet_lines_map,
            "multiplied_timesheet_hours": multiplied_timesheet_hours,
            "multiplied_total_hours": multiplied_total_hours,
        })
        return report_values
