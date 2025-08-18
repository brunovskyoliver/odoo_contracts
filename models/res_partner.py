# Copyright 2017 Carlos Dauden <carlos.dauden@tecnativa.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from ast import literal_eval

from odoo import fields, models, api


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_hours_warning_sent = fields.Boolean(string="Hours Warning Sent", default=False)

    @api.onchange('x_annual_available_hours')
    def _onchange_annual_available_hours(self):
        if self.company_type == 'company' and self.x_annual_free_hours:
            threshold = self.x_annual_free_hours * 0.2  # 20% of free hours
            
            if self.x_annual_available_hours <= 0 and not self.x_hours_warning_sent:
                # Hours completely ran out - send final alert
                template = self.env.ref('contract.email_template_hours_depleted')
                template.send_mail(self.id, force_send=True)
                self.x_hours_warning_sent = True
            elif self.x_annual_available_hours <= threshold and not self.x_hours_warning_sent:
                # First time reaching 20% threshold - send initial alert
                template = self.env.ref('contract.email_template_hours_low_warning')
                template.send_mail(self.id, force_send=True)
                self.x_hours_warning_sent = True
            elif self.x_annual_available_hours > threshold:
                # Reset the flag when hours go above threshold (e.g., after renewal)
                self.x_hours_warning_sent = False

    sale_contract_count = fields.Integer(
        string="Sale Contracts",
        compute="_compute_contract_count",
    )
    purchase_contract_count = fields.Integer(
        string="Purchase Contracts",
        compute="_compute_contract_count",
    )
    contract_ids = fields.One2many(
        comodel_name="contract.contract",
        inverse_name="partner_id",
        string="Contracts",
    )

    def _get_partner_contract_domain(self):
        return [("partner_id", "child_of", self.ids)]

    def _compute_contract_count(self):
        contract_model = self.env["contract.contract"]
        fetch_data = contract_model.read_group(
            self._get_partner_contract_domain(),
            ["partner_id", "contract_type"],
            ["partner_id", "contract_type"],
            lazy=False,
        )
        result = [
            [data["partner_id"][0], data["contract_type"], data["__count"]]
            for data in fetch_data
        ]
        for partner in self:
            partner_child_ids = partner.child_ids.ids + partner.ids
            partner.sale_contract_count = sum(
                r[2] for r in result if r[0] in partner_child_ids and r[1] == "sale"
            )
            partner.purchase_contract_count = sum(
                r[2] for r in result if r[0] in partner_child_ids and r[1] == "purchase"
            )

    def act_show_contract(self):
        """This opens contract view
        @return: the contract view
        """
        self.ensure_one()
        contract_type = self._context.get("contract_type")

        res = self._get_act_window_contract_xml(contract_type)
        action_context = {k: v for k, v in self.env.context.items() if k != "group_by"}
        action_context["default_partner_id"] = self.id
        action_context["default_pricelist_id"] = self.property_product_pricelist.id
        res["context"] = action_context
        res["domain"] = (
            literal_eval(res["domain"]) + self._get_partner_contract_domain()
        )
        return res

    def _get_act_window_contract_xml(self, contract_type):
        if contract_type == "purchase":
            return self.env["ir.actions.act_window"]._for_xml_id(
                "contract.action_supplier_contract"
            )
        else:
            return self.env["ir.actions.act_window"]._for_xml_id(
                "contract.action_customer_contract"
            )
