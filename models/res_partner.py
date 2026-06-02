# Copyright 2017 Carlos Dauden <carlos.dauden@tecnativa.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from ast import literal_eval

from odoo import fields, models, api, _


class ResPartner(models.Model):
    _inherit = "res.partner"

    service_report_send_mode = fields.Selection(
        selection=[
            ("immediate", "Hneď po zásahu"),
            ("weekly", "Raz týždenne"),
            ("monthly", "Raz mesačne"),
        ],
        string="Posielanie servisných výkazov",
        default="immediate",
        required=True,
        help=(
            "Určuje, kedy sa majú zákazníkovi odosielať servisné výkazy. "
            "Pri kontaktoch pod firmou sa pri automatickom odosielaní používa "
            "nastavenie materskej firmy."
        ),
    )

    def message_update(self, msg_dict, update_vals=None):
        result = super().message_update(msg_dict, update_vals=update_vals)
        for partner in self:
            partner._create_helpdesk_ticket_from_partner_email(msg_dict)
        return result

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        partner = super().message_new(msg_dict, custom_values=custom_values)
        partner._create_helpdesk_ticket_from_partner_email(msg_dict)
        return partner

    def _create_helpdesk_ticket_from_partner_email(self, msg_dict):
        self.ensure_one()
        if msg_dict.get("message_type") and msg_dict.get("message_type") != "email":
            return
        if "helpdesk.ticket" not in self.env or "helpdesk.team" not in self.env:
            return

        ticket_model = self.env["helpdesk.ticket"]
        author_partner = ticket_model._contract_resolve_inbound_author(
            msg_dict,
            force_create=True,
        )
        if not author_partner:
            return
        if author_partner.user_ids:
            return

        customer = author_partner.commercial_partner_id or self.commercial_partner_id
        if not customer:
            return

        subject = msg_dict.get("subject") or _("Správa od zákazníka")
        ticket_model._contract_create_from_inbound_email(
            msg_dict,
            source_model=self._name,
            source_res_id=self.id,
            name=_("Email od zákazníka: %s") % subject,
            description=msg_dict.get("body") or _("Prijatá správa od zákazníka."),
            partner=customer,
            force_create_partner=True,
        )

    x_hours_warning_sent = fields.Boolean(string="Hours Warning Sent", default=False)
    orange_variabilny_symbol = fields.Char(
        string='Orange Variabilný Symbol',
        help='Variabilný symbol z faktúry Orange, ktorý slúži na spárovanie a refakturáciu',
        copy=False,
        index=True,
    )

    def write(self, vals):
        # Check if we're updating available hours
        if 'x_annual_available_hours' in vals:
            for record in self:
                new_hours = vals['x_annual_available_hours']
                if record.company_type == 'company' and record.x_annual_free_hours:
                    threshold = record.x_annual_free_hours * 0.2
                    
                    if new_hours <= 0:
                        # Hours completely ran out - send final alert
                        template = self.env.ref('contract.email_template_hours_depleted')
                        template.send_mail(record.id, force_send=True)
                        vals['x_hours_warning_sent'] = True
                    elif new_hours <= threshold and not record.x_hours_warning_sent:
                        # First time reaching 20% threshold - send initial alert
                        template = self.env.ref('contract.email_template_hours_low_warning')
                        template.send_mail(record.id, force_send=True)
                        vals['x_hours_warning_sent'] = True
                    elif new_hours > threshold:
                        # Reset the warning flag when hours go above threshold
                        vals['x_hours_warning_sent'] = False
        
        return super(ResPartner, self).write(vals)

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
