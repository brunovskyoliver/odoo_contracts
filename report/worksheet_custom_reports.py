# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, models


class TaskCustomReport(models.AbstractModel):
    _inherit = "report.industry_fsm.worksheet_custom"

    @api.model
    def _get_report_values(self, docids, data=None):
        report_values = super()._get_report_values(docids, data)
        timesheet_lines_map = {}
        multiplied_timesheet_hours = {}
        multiplied_total_hours = {}

        for task in report_values.get("docs"):
            multiplier = task.project_id.customer_hours_multiplier or 1.0
            if task.project_id.exclude_from_customer_hours:
                timesheet_lines_map[task.id] = task.timesheet_ids.browse()
                multiplied_total_hours[task.id] = 0.0
                continue

            timesheet_lines_map[task.id] = task.timesheet_ids
            for line in task.timesheet_ids:
                multiplied_timesheet_hours[line.id] = line.unit_amount * multiplier
            multiplied_total_hours[task.id] = task.effective_hours * multiplier

        report_values.update({
            "timesheet_lines_map": timesheet_lines_map,
            "multiplied_timesheet_hours": multiplied_timesheet_hours,
            "multiplied_total_hours": multiplied_total_hours,
        })
        return report_values
