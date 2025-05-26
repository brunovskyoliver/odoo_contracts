# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _


class ContractMobileService(models.Model):
    _name = "contract.mobile.service"
    _description = "Mobile Service"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Name", required=True, tracking=True)
    phone_number = fields.Char(string="Phone Number", required=True, tracking=True)
    operator = fields.Selection(
        selection=[
            ('telekom', 'Telekom'),
            ('o2', 'O2'),
        ],
        string="Operator",
        required=True,
        tracking=True,
    )
    is_active = fields.Boolean(
        string="Je aktivne",
        default=True,
        tracking=True,
        help="Only active mobile services will be invoiced",
    )
    inventory_id = fields.Many2one(
        comodel_name="contract.inventory",
        string="Inventory",
        required=True,
        tracking=True,
    )
    contract_line_id = fields.Many2one(
        comodel_name="contract.line",
        string="Contract Line",
        tracking=True,
    )
    contract_id = fields.Many2one(
        related="contract_line_id.contract_id",
        string="Contract",
        store=True,
    )
    partner_id = fields.Many2one(
        related="contract_id.partner_id",
        string="Partner",
        store=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
        tracking=True,
    )
    notes = fields.Text(string="Notes")

    def write(self, vals):
        """Update the parent contract line's name when phone number or active status changes"""
        result = super().write(vals)
        # If phone number or active status changed, update the contract line name
        if 'phone_number' in vals or 'is_active' in vals:
            # Group by contract line and call _compute_mobile_service_description
            contract_lines = self.mapped('contract_line_id')
            for contract_line in contract_lines:
                if contract_line and contract_line.is_mobile_service:
                    # Use context to prevent infinite recursion
                    contract_line.with_context(from_mobile_service_update=True)._compute_mobile_service_description()
        return result

    @api.model
    def create(self, vals):
        """Update the parent contract line's name when a new mobile service is created"""
        record = super().create(vals)
        if record.contract_line_id and record.contract_line_id.is_mobile_service:
            # Use context to prevent infinite recursion
            record.contract_line_id.with_context(from_mobile_service_update=True)._compute_mobile_service_description()
        return record
