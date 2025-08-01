# Copyright 2004-2010 OpenERP SA
# Copyright 2014 Angel Moya <angel.moya@domatix.com>
# Copyright 2015-2020 Tecnativa - Pedro M. Baeza
# Copyright 2016-2018 Tecnativa - Carlos Dauden
# Copyright 2016-2017 LasLabs Inc.
# Copyright 2018 ACSONE SA/NV
# Copyright 2021 Tecnativa - Víctor Martínez
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)


class ContractContract(models.Model):
    _name = "contract.contract"
    _description = "Contract"
    _order = "code, name asc"
    _inherit = [
        "mail.thread",
        "mail.activity.mixin",
        "contract.abstract.contract",
        "contract.recurrency.mixin",
        "portal.mixin",
    ]

    active = fields.Boolean(
        default=True,
    )
    #code = fields.Char(
    #    string="Reference",
    #)
    code = fields.Char(
        string="Referencia",
        readonly=True,
        copy=False,
        default=lambda self: _('New'),
    )
    
    mobile_usage_report_ids = fields.One2many(
        comodel_name='ir.attachment',
        inverse_name='res_id',
        domain=[('res_model', '=', 'contract.contract'), ('mimetype', '=', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')],
        string='Mobile Usage Reports',
    )

    group_id = fields.Many2one(
        string="Group",
        comodel_name="account.analytic.account",
        ondelete="restrict",
    )
    currency_id = fields.Many2one(
        compute="_compute_currency_id",
        inverse="_inverse_currency_id",
        comodel_name="res.currency",
        string="Mena",
    )
    manual_currency_id = fields.Many2one(
        comodel_name="res.currency",
        readonly=True,
    )
    contract_template_id = fields.Many2one(
        string="Zmluvná predloha", comodel_name="contract.template"
    )
    contract_line_ids = fields.One2many(
        string="Položky zmluvy",
        comodel_name="contract.line",
        inverse_name="contract_id",
        copy=True,
        context={"active_test": False},
    )
    
    total_subtotal = fields.Monetary(
        string='Sucet na fakture bez DPH',
        compute='_compute_total_subtotal',
        store=True,
        currency_field='currency_id',
    )

    # Trick for being able to have 2 different views for the same o2m
    # We need this as one2many widget doesn't allow to define in the view
    # the same field 2 times with different views. 2 views are needed because
    # one of them must be editable inline and the other not, which can't be
    # parametrized through attrs.
    contract_line_fixed_ids = fields.One2many(
        string="Contract lines (fixed)",
        comodel_name="contract.line",
        inverse_name="contract_id",
        context={"active_test": False},
    )

    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Uzatvárateľ",
        index=True,
        default=lambda self: self.env.user,
    )
    create_invoice_visibility = fields.Boolean(
        compute="_compute_create_invoice_visibility"
    )
    date_end = fields.Date(compute="_compute_date_end", store=True, readonly=False)
    payment_term_id = fields.Many2one(
        comodel_name="account.payment.term", string="Platobné podmienky", index=True
    )
    invoice_count = fields.Integer(compute="_compute_invoice_count")
    fiscal_position_id = fields.Many2one(
        comodel_name="account.fiscal.position",
        string="Fiskálna politika",
        ondelete="restrict",
    )
    invoice_partner_id = fields.Many2one(
        string="Fakturačný kontakt",
        comodel_name="res.partner",
        ondelete="restrict",
        domain="['|',('id', 'parent_of', partner_id), ('id', 'child_of', partner_id)]",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner", inverse="_inverse_partner_id", required=True
    )

    commercial_partner_id = fields.Many2one(
        "res.partner",
        compute_sudo=True,
        related="partner_id.commercial_partner_id",
        store=True,
        string="Commercial Entity",
        index=True,
    )
    tag_ids = fields.Many2many(comodel_name="contract.tag", string="Tagy")
    note = fields.Text(string="Poznámky")
    is_terminated = fields.Boolean(string="Terminated", readonly=True, copy=False)
    terminate_reason_id = fields.Many2one(
        comodel_name="contract.terminate.reason",
        string="Termination Reason",
        ondelete="restrict",
        readonly=True,
        copy=False,
        tracking=True,
    )
    terminate_comment = fields.Text(
        string="Termination Comment",
        readonly=True,
        copy=False,
        tracking=True,
    )
    terminate_date = fields.Date(
        string="Termination Date",
        readonly=True,
        copy=False,
        tracking=True,
    )
    modification_ids = fields.One2many(
        comodel_name="contract.modification",
        inverse_name="contract_id",
        string="Úpravy",
    )
    inventory_id = fields.Many2one(
        comodel_name="contract.inventory",
        string="Inventory Storage",
        tracking=True,
    )
    has_inventory_products = fields.Boolean(
        string="Has Inventory Products",
        compute="_compute_has_inventory_products",
        store=True,
    )
    nadspotreba = fields.Boolean(string="Nadspotreba", default=False, store=True)

    show_nadspotreba = fields.Boolean(compute="_compute_show_nadspotreba")

    def _compute_show_nadspotreba(self):
        for rec in self:
            contract_count = self.env['contract.contract'].search_count([
                ('partner_id', '=', rec.partner_id.id),
                ('x_contract_type', '=', 'Mobilky')
            ])
            rec.show_nadspotreba = contract_count == 1

    x_contract_type = fields.Selection(
        selection=[
            ('Prenájom', 'Prenájom'),
            ('Služby', 'Služby'),
            ('Mobilky', 'Mobilky'),
        ],
        string="Typ zmluvy",
        default='Služby',
        required=True,
        tracking=True,
    )
    
    @api.depends('contract_line_ids.in_inventory', 'contract_line_ids')
    def _compute_has_inventory_products(self):
        for contract in self:
            contract.has_inventory_products = any(contract.contract_line_ids.mapped('in_inventory'))

    def get_formview_id(self, access_uid=None):
        if self.contract_type == "sale":
            return self.env.ref("contract.contract_contract_customer_form_view").id
        else:
            return self.env.ref("contract.contract_contract_supplier_form_view").id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code', _('New')) == _('New'):
                vals['code'] = self.env['ir.sequence'].next_by_code('contract.contract') or _('New')
        records = super().create(vals_list)
       # records._set_start_contract_modification()
        return records

    def write(self, vals):
        if vals.get("modification_ids"):
            res = (
                self.env["contract.modification"]
                .browse(vals.get("modification_ids")[0][2])
                .write(vals)
            )
            self._modification_mail_send()
        else:
            res = super().write(vals)
            
        # If the main x_datum_viazanost is changed, update all contract lines
        if 'x_datum_viazanost' in vals:
            for contract in self:
                contract.contract_line_ids.write({'x_datum_viazanosti_produktu': vals['x_datum_viazanost']})
                
        # If date_start is changed, update all contract lines
        if 'date_start' in vals:
            for contract in self:
                contract.contract_line_ids.write({'date_start': vals['date_start']})
                
        # Only propagate recurring_next_date to all lines if line_recurrence is False
        # and if it's not coming from a single line update
        if 'recurring_next_date' in vals:
            for contract in self:
                if (not contract.line_recurrence and 
                    not self.env.context.get('no_contract_next_date_update')):
                    for line in contract.contract_line_ids:
                        line.recurring_next_date = vals['recurring_next_date']
        return res

    @api.model
    def _set_start_contract_modification(self):
        subtype_id = self.env.ref("contract.mail_message_subtype_contract_modification")
        for record in self:
            if record.contract_line_ids:
                date_start = min(record.contract_line_ids.mapped("date_start"))
            else:
                date_start = record.create_date
            record.message_subscribe(
                partner_ids=[record.partner_id.id], subtype_ids=[subtype_id.id]
            )
            record.with_context(skip_modification_mail=True).write(
                {
                    "modification_ids": [
                        (0, 0, {"date": date_start, "description": _("Contract start")})
                    ]
                }
            )

    @api.model
    def _modification_mail_send(self):
        for record in self:
            modification_ids_not_sent = record.modification_ids.filtered(
                lambda x: not x.sent
            )
            if modification_ids_not_sent:
                if not self.env.context.get("skip_modification_mail"):
                    subtype_id = self.env["ir.model.data"]._xmlid_to_res_id(
                        "contract.mail_message_subtype_contract_modification"
                    )
                    template_id = self.env.ref(
                        "contract.mail_template_contract_modification"
                    )
                    record.message_post_with_source(
                        template_id,
                        subtype_id=subtype_id,
                    )
                modification_ids_not_sent.write({"sent": True})

    def _compute_access_url(self):
        for record in self:
            record.access_url = f"/my/contracts/{record.id}"

    def action_preview(self):
        """Invoked when 'Preview' button in contract form view is clicked."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "target": "self",
            "url": self.get_portal_url(),
        }

    def _inverse_partner_id(self):
        for rec in self:
            if not rec.invoice_partner_id:
                rec.invoice_partner_id = rec.partner_id.address_get(["invoice"])[
                    "invoice"
                ]

    def _get_related_invoices(self):
        self.ensure_one()

        invoices = (
            self.env["account.move.line"]
            .search(
                [
                    (
                        "contract_line_id",
                        "in",
                        self.contract_line_ids.ids,
                    )
                ]
            )
            .mapped("move_id")
        )
        # we are forced to always search for this for not losing possible <=v11
        # generated invoices
        invoices |= self.env["account.move"].search([("old_contract_id", "=", self.id)])
        return invoices

    def _get_computed_currency(self):
        """Helper method for returning the theoretical computed currency."""
        self.ensure_one()
        currency = self.env["res.currency"]
        if any(self.contract_line_ids.mapped("automatic_price")):
            # Use pricelist currency
            currency = (
                self.pricelist_id.currency_id
                or self.partner_id.with_company(
                    self.company_id
                ).property_product_pricelist.currency_id
            )
        return currency or self.journal_id.currency_id or self.company_id.currency_id

    @api.depends(
        "manual_currency_id",
        "pricelist_id",
        "partner_id",
        "journal_id",
        "company_id",
    )
    def _compute_currency_id(self):
        for rec in self:
            if rec.manual_currency_id:
                rec.currency_id = rec.manual_currency_id
            else:
                rec.currency_id = rec._get_computed_currency()

    def _inverse_currency_id(self):
        """If the currency is different from the computed one, then save it
        in the manual field.
        """
        for rec in self:
            if rec._get_computed_currency() != rec.currency_id:
                rec.manual_currency_id = rec.currency_id
            else:
                rec.manual_currency_id = False

    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec._get_related_invoices())

   # def action_show_invoices(self):
   #     self.ensure_one()
   #     tree_view = self.env.ref("account.view_invoice_tree", raise_if_not_found=False)
   #     form_view = self.env.ref("account.view_move_form", raise_if_not_found=False)
   #     ctx = dict(self.env.context)
   #     if ctx.get("default_contract_type"):
   #         ctx["default_move_type"] = (
   #             "out_invoice"
   #             if ctx.get("default_contract_type") == "sale"
   #             else "in_invoice"
   #         )
   #     action = {
   #         "type": "ir.actions.act_window",
   #         "name": "Invoices",
   #         "res_model": "account.move",
   #         "view_mode": "tree,kanban,form,calendar,pivot,graph,activity",
   #         "domain": [("id", "in", self._get_related_invoices().ids)],
   #         "context": ctx,
   #     }
   #     if tree_view and form_view:
   #         action["views"] = [(tree_view.id, "tree"), (form_view.id, "form")]
   #     return action

    def action_show_invoices(self):
        self.ensure_one()
        list_view = self.env.ref("account.view_invoice_list", raise_if_not_found=False)  # Adjust to Odoo v18 view
        form_view = self.env.ref("account.view_move_form", raise_if_not_found=False)
        ctx = dict(self.env.context)

        if ctx.get("default_contract_type"):
            ctx["default_move_type"] = (
                "out_invoice"
                if ctx.get("default_contract_type") == "sale"
                else "in_invoice"
            )

        action = {
            "type": "ir.actions.act_window",
            "name": "Invoices",
            "res_model": "account.move",
            "view_mode": "list,kanban,form,pivot,graph,activity",
            "domain": [("id", "in", self._get_related_invoices().ids)],
            "context": ctx,
        }

        if list_view and form_view:
            action["views"] = [(list_view.id, "list"), (form_view.id, "form")]
        return action



    @api.depends("contract_line_ids.date_end")
    def _compute_date_end(self):
        for contract in self:
            contract.date_end = False
            date_end = contract.contract_line_ids.mapped("date_end")
            if date_end and all(date_end):
                contract.date_end = max(date_end)

    @api.depends(
        "contract_line_ids.recurring_next_date",
        "contract_line_ids.is_canceled",
    )
    # pylint: disable=missing-return
    def _compute_recurring_next_date(self):
        for contract in self:
            # Skip recomputation if we're updating a single line
            if self.env.context.get('skip_contract_recurring_next_date'):
                continue
                
            recurring_next_date = contract.contract_line_ids.filtered(
                lambda line: (
                    line.recurring_next_date
                    and not line.is_canceled
                    and (not line.display_type or line.is_recurring_note)
                )
            ).mapped("recurring_next_date")
            # we give priority to computation from date_start if modified
            if (
                contract._origin
                and contract._origin.date_start != contract.date_start
                or not recurring_next_date
            ):
                super(ContractContract, contract)._compute_recurring_next_date()
            else:
                contract.recurring_next_date = min(recurring_next_date)

    @api.depends("contract_line_ids.create_invoice_visibility")
    def _compute_create_invoice_visibility(self):
        for contract in self:
            contract.create_invoice_visibility = any(
                contract.contract_line_ids.mapped("create_invoice_visibility")
            )

    @api.onchange("contract_template_id")
    def _onchange_contract_template_id(self):
        """Update the contract fields with that of the template.

        Take special consideration with the `contract_line_ids`,
        which must be created using the data from the contract lines. Cascade
        deletion ensures that any errant lines that are created are also
        deleted.
        """
        contract_template_id = self.contract_template_id
        if not contract_template_id:
            return
        for field_name, field in contract_template_id._fields.items():
            if field.name == "contract_line_ids":
                lines = self._convert_contract_lines(contract_template_id)
                self.contract_line_ids += lines
            elif not any(
                (
                    field.compute,
                    field.related,
                    field.automatic,
                    field.readonly,
                    field.company_dependent,
                    field.name in self.NO_SYNC,
                )
            ):
                if self.contract_template_id[field_name]:
                    self[field_name] = self.contract_template_id[field_name]

    @api.onchange("partner_id", "company_id")
    def _onchange_partner_id(self):
        partner = (
            self.partner_id
            if not self.company_id
            else self.partner_id.with_company(self.company_id)
        )
        self.pricelist_id = partner.property_product_pricelist.id
        self.fiscal_position_id = partner.env[
            "account.fiscal.position"
        ]._get_fiscal_position(partner)
        if self.contract_type == "purchase":
            self.payment_term_id = partner.property_supplier_payment_term_id
        else:
            self.payment_term_id = partner.property_payment_term_id
        self.invoice_partner_id = self.partner_id.address_get(["invoice"])["invoice"]

    def _convert_contract_lines(self, contract):
        self.ensure_one()
        new_lines = self.env["contract.line"]
        contract_line_model = self.env["contract.line"]
        for contract_line in contract.contract_line_ids:
            vals = contract_line._convert_to_write(contract_line.read()[0])
            # Remove template link field
            vals.pop("contract_template_id", False)
            vals["date_start"] = fields.Date.context_today(contract_line)
            # Only set recurring_next_date if not already set
            if not vals.get("recurring_next_date"):
                vals["recurring_next_date"] = fields.Date.context_today(contract_line)
            new_lines += contract_line_model.new(vals)
        new_lines._onchange_is_auto_renew()
        return new_lines

    def _prepare_invoice(self, date_invoice, journal=None):
        """Prepare the values for the generated invoice record.

        :return: A vals dictionary
        """
        self.ensure_one()
        if not journal:
            journal = (
                self.journal_id
                if self.journal_id.type == self.contract_type
                else self.env["account.journal"].search(
                    [
                        ("type", "=", self.contract_type),
                        ("company_id", "=", self.company_id.id),
                    ],
                    limit=1,
                )
            )
        if not journal:
            raise ValidationError(
                _(
                    "Please define a %(contract_type)s journal "
                    "for the company '%(company)s'."
                )
                % {
                    "contract_type": self.contract_type,
                    "company": self.company_id.name or "",
                }
            )
        invoice_type = (
            "in_invoice" if self.contract_type == "purchase" else "out_invoice"
        )
        vals = {
            "move_type": invoice_type,
            "company_id": self.company_id.id,
            "partner_id": self.invoice_partner_id.id,
            "ref": self.code,
            "currency_id": self.currency_id.id,
            "invoice_date": date_invoice,
            "taxable_supply_date": date_invoice,  # Setting taxable_supply_date to match invoice_date
            "journal_id": journal.id,
            "invoice_origin": self.name,
            "invoice_line_ids": [],
        }
        if self.payment_term_id:
            vals.update(
                {
                    "invoice_payment_term_id": self.payment_term_id.id,
                }
            )
        if self.fiscal_position_id:
            vals.update(
                {
                    "fiscal_position_id": self.fiscal_position_id.id,
                }
            )
        if invoice_type == "out_invoice" and self.user_id:
            vals.update(
                {
                    "invoice_user_id": self.user_id.id,
                }
            )
        return vals

    def action_contract_send(self):
        self.ensure_one()
        template = self.env.ref("contract.email_contract_template", False)
        compose_form = self.env.ref("mail.email_compose_message_wizard_form")
        ctx = dict(
            default_model="contract.contract",
            default_res_ids=self.ids,
            default_use_template=bool(template),
            default_template_id=template and template.id or False,
            default_composition_mode="comment",
        )
        return {
            "name": _("Compose Email"),
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "mail.compose.message",
            "views": [(compose_form.id, "form")],
            "view_id": compose_form.id,
            "target": "new",
            "context": ctx,
        }

    @api.model
    def _get_contracts_to_invoice_domain(self, date_ref=None):
        """
        This method builds the domain to use to find all
        contracts (contract.contract) to invoice.
        :param date_ref: optional reference date to use instead of today
        :return: list (domain) usable on contract.contract
        """
        domain = []
        if not date_ref:
            date_ref = fields.Date.context_today(self)
        domain.extend([("recurring_next_date", "<=", date_ref)])
        return domain

    def _get_lines_to_invoice(self, date_ref):
        """
        This method fetches and returns the lines to invoice on the contract
        (self), based on the given date.
        :param date_ref: date used as reference date to find lines to invoice
        :return: contract lines (contract.line recordset)
        """
        self.ensure_one()

        def can_be_invoiced(contract_line):
            return (
                not contract_line.is_canceled
                and contract_line.recurring_next_date
                and contract_line.recurring_next_date <= date_ref
                and contract_line.next_period_date_start
            )

        lines2invoice = previous = self.env["contract.line"]
        current_section = current_note = False
        for line in self.contract_line_ids:
            if line.display_type == "line_section":
                current_section = line
            elif line.display_type == "line_note" and not line.is_recurring_note:
                if line.note_invoicing_mode == "with_previous_line":
                    if previous in lines2invoice:
                        lines2invoice |= line
                    current_note = False
                elif line.note_invoicing_mode == "with_next_line":
                    current_note = line
            elif line.is_recurring_note or not line.display_type:
                if can_be_invoiced(line):
                    if current_section:
                        lines2invoice |= current_section
                        current_section = False
                    if current_note:
                        lines2invoice |= current_note
                    lines2invoice |= line
                    current_note = False
            previous = line
        return lines2invoice.sorted()

    def _round_price_to_two_places(self, price):
        """Round price to two decimal places, preserving 4-decimal format with zeros"""
        if price is not False and price is not None:
            # Round to 2 decimals but keep 4-decimal format
            return round(float(price), 2)
        return price

    def _prepare_recurring_invoices_values(self, date_ref=False):
        """
        This method builds the list of invoices values to create, based on
        the lines to invoice of the contracts in self.
        !!! The date of next invoice (recurring_next_date) is updated here !!!
        :return: list of dictionaries (invoices values)
        """
        invoices_values = []
        for contract in self:
            if not date_ref:
                date_ref = contract.recurring_next_date
            if not date_ref:
                # this use case is possible when recurring_create_invoice is
                # called for a finished contract
                continue
            contract_lines = contract._get_lines_to_invoice(date_ref)
            if not contract_lines:
                continue
            invoice_vals = contract._prepare_invoice(date_ref)
            invoice_vals["invoice_line_ids"] = []
            for line in contract_lines:
                invoice_line_vals = line._prepare_invoice_line()
                # Apply discounted price (x_zlavnena_cena) if commitment date is in the future
                from datetime import date as _date
                today = _date.today()
                if (
                    invoice_line_vals
                    and line.x_zlavnena_cena is not False
                    and line.x_datum_viazanosti_produktu
                    and line.x_datum_viazanosti_produktu >= today
                ):
                    invoice_line_vals['price_unit'] = self._round_price_to_two_places(line.x_zlavnena_cena)
                elif invoice_line_vals and 'price_unit' in invoice_line_vals:
                    invoice_line_vals['price_unit'] = self._round_price_to_two_places(invoice_line_vals['price_unit'])
                    
                if invoice_line_vals:
                    # Allow extension modules to return an empty dictionary for
                    # nullifying line. We should then cleanup certain values.
                    if "company_id" in invoice_line_vals:
                        del invoice_line_vals["company_id"]
                    if "company_currency_id" in invoice_line_vals:
                        del invoice_line_vals["company_currency_id"]
                    invoice_vals["invoice_line_ids"].append(
                        Command.create(invoice_line_vals)
                    )
            invoices_values.append(invoice_vals)
            # Force the recomputation of journal items
            contract_lines._update_recurring_next_date()
        return invoices_values

    def recurring_create_invoice(self):
        """
        This method triggers the creation of the next invoices of the contracts
        even if their next invoicing date is in the future.
        """
        invoices = self._recurring_create_invoice()
        for invoice in invoices:
            body = Markup(_("Contract manually invoiced: %(invoice_link)s")) % {
                "invoice_link": invoice._get_html_link(title=invoice.name)
            }
            self.message_post(body=body)
        return invoices

    @api.model
    def _invoice_followers(self, invoices):
        invoice_create_subtype = self.env.ref(
            "contract.mail_message_subtype_invoice_created"
        )
        for item in self:
            partner_ids = item.message_follower_ids.filtered(
                lambda x: invoice_create_subtype in x.subtype_ids
            ).mapped("partner_id")
            if partner_ids:
                (invoices & item._get_related_invoices()).message_subscribe(
                    partner_ids=partner_ids.ids
                )

    @api.model
    def _add_contract_origin(self, invoices):
        for item in self:
            for move in invoices & item._get_related_invoices():
                translation = _("by contract")
                move.message_post(
                    body=Markup(
                        f"{move._creation_message()} {translation} "
                        f"{item._get_html_link(title=item.display_name)}."
                    )
                )

    def _recurring_create_invoice(self, date_ref=False):
        invoices_values = self._prepare_recurring_invoices_values(date_ref)
        moves = self.env["account.move"].create(invoices_values)
        self._add_contract_origin(moves)
        self._invoice_followers(moves)
        
        # For each contract, copy its mobile usage reports to its corresponding invoice
        for contract in self:
            contract_moves = moves & contract._get_related_invoices()
            for move in contract_moves:
                contract._copy_mobile_usage_reports_to_invoice(move)
        
        self._compute_recurring_next_date()
        # Post the invoices automatically
        moves.action_post()
        return moves

    @api.model
    def _get_recurring_create_func(self, create_type="invoice"):
        """
        Allows to retrieve the recurring create function depending
        on generate_type attribute
        """
        if create_type == "invoice":
            return self.__class__._recurring_create_invoice

    @api.model
    def _cron_recurring_create(self, date_ref=False, create_type="invoice"):
        """
        The cron function in order to create recurrent documents
        from contracts.
        """
        _recurring_create_func = self._get_recurring_create_func(
            create_type=create_type
        )
        if not date_ref:
            date_ref = fields.Date.context_today(self)
            
        # First check and update commitments for product lines with expired commitment dates
        all_lines = self.env['contract.line'].search([
            ('x_datum_viazanosti_produktu', '<', date_ref),
            ('x_datum_viazanosti_produktu', '!=', False)
        ])
        if all_lines:
            all_lines._compute_commitment_discount()
            all_lines._compute_price_subtotal()
        
        # Continue with regular invoice creation
        domain = self._get_contracts_to_invoice_domain(date_ref)
        domain = expression.AND(
            [
                domain,
                [("generation_type", "=", create_type)],
            ]
        )
        contracts = self.search(domain)
        companies = set(contracts.mapped("company_id"))
        
        # Invoice by companies, so assignation emails get correct context
        for company in companies:
            contracts_to_invoice = contracts.filtered(
                lambda contract, comp=company: contract.company_id == comp
                and (
                    not contract.date_end
                    or contract.recurring_next_date <= contract.date_end
                )
            ).with_company(company)
            _recurring_create_func(contracts_to_invoice, date_ref)
        return True

    @api.model
    def cron_recurring_create_invoice(self, date_ref=None):
        return self._cron_recurring_create(date_ref, create_type="invoice")

    def action_terminate_contract(self):
        self.ensure_one()
        context = {"default_contract_id": self.id}
        return {
            "type": "ir.actions.act_window",
            "name": _("Terminate Contract"),
            "res_model": "contract.contract.terminate",
            "view_mode": "form",
            "target": "new",
            "context": context,
        }

    def _terminate_contract(
        self,
        terminate_reason_id,
        terminate_comment,
        terminate_date,
        terminate_lines_with_last_date_invoiced=False,
    ):
        self.ensure_one()
        if not self.env.user.has_group("contract.can_terminate_contract"):
            raise UserError(_("You are not allowed to terminate contracts."))
        for line in self.contract_line_ids.filtered("is_stop_allowed"):
            line.stop(
                max(terminate_date, line.last_date_invoiced)
                if terminate_lines_with_last_date_invoiced and line.last_date_invoiced
                else terminate_date
            )
        self.write(
            {
                "is_terminated": True,
                "terminate_reason_id": terminate_reason_id.id,
                "terminate_comment": terminate_comment,
                "terminate_date": terminate_date,
            }
        )
        return True

    def action_cancel_contract_termination(self):
        self.ensure_one()
        self.write(
            {
                "is_terminated": False,
                "terminate_reason_id": False,
                "terminate_date": False,
                "terminate_comment": False,
            }
        )

    @api.depends('contract_line_ids.price_subtotal')
    def _compute_total_subtotal(self):
        for contract in self:
            contract.total_subtotal = sum(contract.contract_line_ids.mapped('price_subtotal'))
            
    def action_get_attachment_tree_view(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % self.id,
            'target': 'self',
        }
    
    def _copy_mobile_usage_reports_to_invoice(self, invoice):
        """Copy latest mobile usage report from contract to invoice if this is a Mobilky contract and set as main attachment"""
        self.ensure_one()
        if self.x_contract_type == 'Mobilky' and self.mobile_usage_report_ids:
            # Get the latest mobile usage report
            latest_report = self.mobile_usage_report_ids.sorted(lambda r: r.create_date, reverse=True)[0]
            # Create the attachment on the invoice
            if not latest_report.name.lower().endswith('.xlsx'):
                latest_report.name += '.xlsx'
            new_attachment = self.env['ir.attachment'].create({
                'name': latest_report.name,
                'type': 'binary',
                'datas': latest_report.datas,
                'res_model': 'account.move',
                'res_id': invoice.id,
                'mimetype': latest_report.mimetype,
                'description': f"Mobile Usage Report copied from contract {self.name}"
            })
            # Set it as the main attachment so it's included in mail notifications
            invoice.message_main_attachment_id = new_attachment