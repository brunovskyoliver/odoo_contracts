# Copyright 2017 LasLabs Inc.
# Copyright 2018 ACSONE SA/NV.
# Copyright 2020 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .contract_line_constraints import get_allowed


class ContractLine(models.Model):
    _name = "contract.line"
    _description = "Contract Line"
    _inherit = [
        "contract.abstract.contract.line",
        "contract.recurrency.mixin",
        "analytic.mixin",
    ]
    _order = "sequence,id"

    sequence = fields.Integer()
    contract_id = fields.Many2one(
        comodel_name="contract.contract",
        string="Contract",
        required=True,
        index=True,
        auto_join=True,
        ondelete="cascade",
    )
    currency_id = fields.Many2one(related="contract_id.currency_id")
    date_start = fields.Date(required=True)
    date_end = fields.Date(compute="_compute_date_end", store=True, readonly=False)
    termination_notice_date = fields.Date(
        compute="_compute_termination_notice_date",
        store=True,
        copy=False,
    )
    create_invoice_visibility = fields.Boolean(
        compute="_compute_create_invoice_visibility"
    )
    successor_contract_line_id = fields.Many2one(
        comodel_name="contract.line",
        string="Successor Contract Line",
        required=False,
        readonly=True,
        index=True,
        copy=False,
        help="In case of restart after suspension, this field contain the new "
        "contract line created.",
    )
    predecessor_contract_line_id = fields.Many2one(
        comodel_name="contract.line",
        string="Predecessor Contract Line",
        required=False,
        readonly=True,
        index=True,
        copy=False,
        help="Contract Line origin of this one.",
    )
    manual_renew_needed = fields.Boolean(
        default=False,
        help="This flag is used to make a difference between a definitive stop"
        "and temporary one for which a user is not able to plan a"
        "successor in advance",
    )
    in_inventory = fields.Boolean(
        string="Track in Inventory",
        default=False,
        help="If checked, the product will be tracked in inventory",
    )
    is_mobile_service = fields.Boolean(
        string="Mobilka",
        default=False,
        help="If checked, this contract line represents a mobile service",
    )
    mobile_service_ids = fields.One2many(
        comodel_name="contract.mobile.service",
        inverse_name="contract_line_id",
        string="Mobile Services",
    )
    mobile_service_count = fields.Integer(
        string="Mobile Service Count",
        compute="_compute_mobile_service_count",
    )
    inventory_line_ids = fields.One2many(
        comodel_name="contract.inventory.line",
        inverse_name="contract_line_id",
        string="Inventory Lines",
    )
    inventory_quantity = fields.Float(
        string="Inventory Quantity",
        compute="_compute_inventory_quantity",
    )
    inventory_state = fields.Selection(
        selection=[
            ('not_tracked', 'Not Tracked'),
            ('pending', 'Pending'),
            ('allocated', 'Allocated'),
            ('partial', 'Partially Allocated'),
        ],
        string="Inventory Status",
        compute="_compute_inventory_state",
        store=True,
    )
    is_plan_successor_allowed = fields.Boolean(
        string="Plan successor allowed?", compute="_compute_allowed"
    )
    is_stop_plan_successor_allowed = fields.Boolean(
        string="Stop/Plan successor allowed?", compute="_compute_allowed"
    )
    is_stop_allowed = fields.Boolean(string="Stop allowed?", compute="_compute_allowed")
    is_cancel_allowed = fields.Boolean(
        string="Cancel allowed?", compute="_compute_allowed"
    )
    is_un_cancel_allowed = fields.Boolean(
        string="Un-Cancel allowed?", compute="_compute_allowed"
    )
    state = fields.Selection(
        selection=[
            ("upcoming", "Upcoming"),
            ("in-progress", "In-progress"),
            ("to-renew", "To renew"),
            ("upcoming-close", "Upcoming Close"),
            ("closed", "Closed"),
            ("canceled", "Canceled"),
        ],
        compute="_compute_state",
        search="_search_state",
    )
    active = fields.Boolean(
        string="Active",
        related="contract_id.active",
        store=True,
        readonly=True,
    )

    @api.depends(
        "last_date_invoiced",
        "date_start",
        "date_end",
    )
    # pylint: disable=missing-return
    def _compute_next_period_date_start(self):
        """Compute the start date for the next period"""
        for rec in self:
            # Skip if we're in a no_recompute context
            if self.env.context.get('no_next_period_recompute'):
                continue
                
            if rec.last_date_invoiced:
                next_period_date_start = rec.last_date_invoiced + relativedelta(days=1)
            else:
                next_period_date_start = rec.date_start
            if (
                rec.date_end
                and next_period_date_start
                and next_period_date_start > rec.date_end
            ):
                next_period_date_start = False
            rec.next_period_date_start = next_period_date_start

    @api.depends(
        "next_period_date_start",
        "recurring_rule_type",
        "recurring_interval",
        "date_end",
    )
    def _compute_next_period_date_end(self):
        """Compute the end date for the next period"""
        for rec in self:
            # Skip if we're in a no_recompute context
            if self.env.context.get('no_next_period_recompute'):
                continue
                
            rec.next_period_date_end = self.get_next_period_date_end(
                rec.next_period_date_start,
                rec.recurring_rule_type,
                rec.recurring_interval,
                max_date_end=rec.date_end,
                next_invoice_date=rec.recurring_next_date,
                recurring_invoicing_type=rec.recurring_invoicing_type,
                recurring_invoicing_offset=rec.recurring_invoicing_offset,
            )

    @api.depends("next_period_date_start")
    def _compute_recurring_next_date(self):
        """Compute the next invoice date"""
        for rec in self:
            if not rec.next_period_date_start:
                continue
                
            # Use a context to prevent contract recomputation when updating a single line
            rec.with_context(skip_contract_recurring_next_date=True).recurring_next_date = (
                self.get_next_invoice_date(
                    rec.next_period_date_start,
                    rec.recurring_invoicing_type,
                    rec.recurring_invoicing_offset,
                    rec.recurring_rule_type,
                    rec.recurring_interval,
                    max_date_end=rec.date_end,
                )
            )

    @api.depends("contract_id.date_end", "contract_id.line_recurrence")
    def _compute_date_end(self):
        self._set_recurrence_field("date_end")

    @api.depends(
        "date_end",
        "termination_notice_rule_type",
        "termination_notice_interval",
    )
    def _compute_termination_notice_date(self):
        for rec in self:
            if rec.date_end:
                rec.termination_notice_date = rec.date_end - self.get_relative_delta(
                    rec.termination_notice_rule_type,
                    rec.termination_notice_interval,
                )
            else:
                rec.termination_notice_date = False

    @api.depends(
        "is_canceled",
        "date_start",
        "date_end",
        "is_auto_renew",
        "manual_renew_needed",
        "termination_notice_date",
        "successor_contract_line_id",
    )
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.state = False
            if rec.display_type:
                continue
            if rec.is_canceled:
                rec.state = "canceled"
                continue

            if rec.date_start and rec.date_start > today:
                # Before period
                rec.state = "upcoming"
                continue
            if (
                rec.date_start
                and rec.date_start <= today
                and (not rec.date_end or rec.date_end >= today)
            ):
                # In period
                if (
                    rec.termination_notice_date
                    and rec.termination_notice_date < today
                    and not rec.is_auto_renew
                    and not rec.manual_renew_needed
                ):
                    rec.state = "upcoming-close"
                else:
                    rec.state = "in-progress"
                continue
            if rec.date_end and rec.date_end < today:
                # After
                if (
                    rec.manual_renew_needed
                    and not rec.successor_contract_line_id
                    or rec.is_auto_renew
                ):
                    rec.state = "to-renew"
                else:
                    rec.state = "closed"

    @api.model
    def _get_state_domain(self, state):
        today = fields.Date.context_today(self)
        if state == "upcoming":
            return [
                "&",
                ("date_start", ">", today),
                ("is_canceled", "=", False),
            ]
        if state == "in-progress":
            return [
                "&",
                "&",
                "&",
                ("date_start", "<=", today),
                ("is_canceled", "=", False),
                "|",
                ("date_end", ">=", today),
                ("date_end", "=", False),
                "|",
                ("is_auto_renew", "=", True),
                "&",
                ("is_auto_renew", "=", False),
                ("termination_notice_date", ">", today),
            ]
        if state == "to-renew":
            return [
                "&",
                "&",
                ("is_canceled", "=", False),
                ("date_end", "<", today),
                "|",
                "&",
                ("manual_renew_needed", "=", True),
                ("successor_contract_line_id", "=", False),
                ("is_auto_renew", "=", True),
            ]
        if state == "upcoming-close":
            return [
                "&",
                "&",
                "&",
                "&",
                "&",
                ("date_start", "<=", today),
                ("is_auto_renew", "=", False),
                ("manual_renew_needed", "=", False),
                ("is_canceled", "=", False),
                ("termination_notice_date", "<", today),
                ("date_end", ">=", today),
            ]
        if state == "closed":
            return [
                "&",
                "&",
                "&",
                ("is_canceled", "=", False),
                ("date_end", "<", today),
                ("is_auto_renew", "=", False),
                "|",
                "&",
                ("manual_renew_needed", "=", True),
                ("successor_contract_line_id", "!=", False),
                ("manual_renew_needed", "=", False),
            ]
        if state == "canceled":
            return [("is_canceled", "=", True)]
        if not state:
            return [("display_type", "!=", False)]

    @api.model
    def _search_state(self, operator, value):
        states = [
            "upcoming",
            "in-progress",
            "to-renew",
            "upcoming-close",
            "closed",
            "canceled",
            False,
        ]
        if operator == "=":
            return self._get_state_domain(value)
        if operator == "!=":
            domain = []
            for state in states:
                if state != value:
                    if domain:
                        domain.insert(0, "|")
                    domain.extend(self._get_state_domain(state))
            return domain
        if operator == "in":
            domain = []
            for state in value:
                if domain:
                    domain.insert(0, "|")
                domain.extend(self._get_state_domain(state))
            return domain

        if operator == "not in":
            if set(value) == set(states):
                return [("id", "=", False)]
            return self._search_state(
                "in", [state for state in states if state not in value]
            )

    @api.depends(
        "date_start",
        "date_end",
        "last_date_invoiced",
        "is_auto_renew",
        "successor_contract_line_id",
        "predecessor_contract_line_id",
        "is_canceled",
        "contract_id.is_terminated",
    )
    def _compute_allowed(self):
        for rec in self:
            rec.update(
                {
                    "is_plan_successor_allowed": False,
                    "is_stop_plan_successor_allowed": False,
                    "is_stop_allowed": False,
                    "is_cancel_allowed": False,
                    "is_un_cancel_allowed": False,
                }
            )
            if rec.contract_id.is_terminated:
                continue
            if rec.date_start:
                allowed = get_allowed(
                    rec.date_start,
                    rec.date_end,
                    rec.last_date_invoiced,
                    rec.is_auto_renew,
                    rec.successor_contract_line_id,
                    rec.predecessor_contract_line_id,
                    rec.is_canceled,
                )
                if allowed:
                    rec.update(
                        {
                            "is_plan_successor_allowed": allowed.plan_successor,
                            "is_stop_plan_successor_allowed": (
                                allowed.stop_plan_successor
                            ),
                            "is_stop_allowed": allowed.stop,
                            "is_cancel_allowed": allowed.cancel,
                            "is_un_cancel_allowed": allowed.uncancel,
                        }
                    )

    @api.constrains("is_auto_renew", "successor_contract_line_id", "date_end")
    def _check_allowed(self):
        """
        logical impossible combination:
            * a line with is_auto_renew True should have date_end and
              couldn't have successor_contract_line_id
            * a line without date_end can't have successor_contract_line_id

        """
        for rec in self:
            if rec.is_auto_renew:
                if rec.successor_contract_line_id:
                    raise ValidationError(
                        _(
                            "A contract line with a successor "
                            "can't be set to auto-renew"
                        )
                    )
                if not rec.date_end:
                    raise ValidationError(_("An auto-renew line must have a end date"))
            else:
                if not rec.date_end and rec.successor_contract_line_id:
                    raise ValidationError(
                        _("A contract line with a successor " "must have a end date")
                    )

    @api.constrains("successor_contract_line_id", "date_end")
    def _check_overlap_successor(self):
        for rec in self:
            if rec.date_end and rec.successor_contract_line_id:
                if rec.date_end >= rec.successor_contract_line_id.date_start:
                    raise ValidationError(
                        _("Contract line and its successor overlapped")
                    )

    @api.constrains("predecessor_contract_line_id", "date_start")
    def _check_overlap_predecessor(self):
        for rec in self:
            if (
                rec.predecessor_contract_line_id
                and rec.predecessor_contract_line_id.date_end
            ):
                if rec.date_start <= rec.predecessor_contract_line_id.date_end:
                    raise ValidationError(
                        _("Contract line and its predecessor overlapped")
                    )

    @api.model
    def _compute_first_recurring_next_date(
        self,
        date_start,
        recurring_invoicing_type,
        recurring_rule_type,
        recurring_interval,
    ):
        # deprecated method for backward compatibility
        return self.get_next_invoice_date(
            date_start,
            recurring_invoicing_type,
            self._get_default_recurring_invoicing_offset(
                recurring_invoicing_type, recurring_rule_type
            ),
            recurring_rule_type,
            recurring_interval,
            max_date_end=False,
        )

    @api.model
    def _get_first_date_end(
        self, date_start, auto_renew_rule_type, auto_renew_interval
    ):
        return (
            date_start
            + self.get_relative_delta(auto_renew_rule_type, auto_renew_interval)
            - relativedelta(days=1)
        )

    @api.onchange(
        "date_start",
        "is_auto_renew",
        "auto_renew_rule_type",
        "auto_renew_interval",
    )
    def _onchange_is_auto_renew(self):
        """Date end should be auto-computed if a contract line is set to
        auto_renew"""
        for rec in self.filtered("is_auto_renew"):
            if rec.date_start:
                rec.date_end = self._get_first_date_end(
                    rec.date_start,
                    rec.auto_renew_rule_type,
                    rec.auto_renew_interval,
                )

    @api.constrains("is_canceled", "is_auto_renew")
    def _check_auto_renew_canceled_lines(self):
        for rec in self:
            if rec.is_canceled and rec.is_auto_renew:
                raise ValidationError(
                    _("A canceled contract line can't be set to auto-renew")
                )

    @api.constrains("recurring_next_date", "date_start")
    def _check_recurring_next_date_start_date(self):
        for line in self:
            # Skip validation if called from mobile invoice settings
            if self.env.context.get('skip_date_check'):
                continue
                
            if (
                line.display_type in ("line_section", "line_note")
                or not line.recurring_next_date
            ):
                continue
            if line.date_start and line.recurring_next_date:
                if line.date_start > line.recurring_next_date:
                    raise ValidationError(
                        _(
                            "You can't have a date of next invoice anterior "
                            "to the start of the contract line '%s'"
                        )
                        % line.name
                    )

    @api.constrains(
        "date_start", "date_end", "last_date_invoiced", "recurring_next_date"
    )
    def _check_last_date_invoiced(self):
        for rec in self.filtered("last_date_invoiced"):
            if not rec.contract_id.line_recurrence:
                continue
            if rec.date_start and rec.date_start > rec.last_date_invoiced:
                raise ValidationError(
                    _(
                        "You can't have the start date after the date of last "
                        "invoice for the contract line '%s'"
                    )
                    % rec.name
                )
            # Removed the recurring_next_date constraint to allow more flexible invoice scheduling

    @api.constrains("recurring_next_date")
    def _check_recurring_next_date_recurring_invoices(self):
        for rec in self:
            if not rec.recurring_next_date and (
                not rec.date_end
                or not rec.last_date_invoiced
                or rec.last_date_invoiced < rec.date_end
            ):
                raise ValidationError(
                    _(
                        "You must supply a date of next invoice for contract "
                        "line '%s'"
                    )
                    % rec.name
                )

    @api.constrains("date_start", "date_end")
    def _check_start_end_dates(self):
        for line in self.filtered("date_end"):
            if line.date_start and line.date_end:
                if line.date_start > line.date_end:
                    raise ValidationError(
                        _(
                            "Contract line '%s' start date can't be later than"
                            " end date"
                        )
                        % line.name
                    )

    @api.depends(
        "display_type",
        "is_recurring_note",
        "recurring_next_date",
        "date_start",
        "date_end",
    )
    def _compute_create_invoice_visibility(self):
        # TODO: depending on the lines, and their order, some sections
        # have no meaning in certain invoices
        today = fields.Date.context_today(self)
        for rec in self:
            if (
                (not rec.display_type or rec.is_recurring_note)
                and rec.date_start
                and today >= rec.date_start
            ):
                rec.create_invoice_visibility = bool(rec.recurring_next_date)
            else:
                rec.create_invoice_visibility = False

    def _prepare_invoice_line(self):
        self.ensure_one()
        dates = self._get_period_to_invoice(
            self.last_date_invoiced, self.recurring_next_date
        )
        name = self._insert_markers(dates[0], dates[1])
        
        # Add commitment discount information to the name if applicable
        if hasattr(self, 'commitment') and self.commitment != 'none' and self.commitment_discount > 0:
            name += _(" (so zľavou z viazanosti: %s)") % self.commitment_discount
        
        # Calculate price with commitment discount
        price_unit = self.price_unit
        if hasattr(self, 'commitment_discount') and self.commitment_discount:
            price_unit = max(0, price_unit - self.commitment_discount)
        
        # For mobile services, only invoice active ones
        quantity = self._get_quantity_to_invoice(*dates)
        if self.is_mobile_service and self.mobile_service_ids:
            active_services = len(self.mobile_service_ids.filtered('is_active'))
            if active_services > 0:
                quantity = active_services
                # Add mobile service info to the name
                operators = self.mobile_service_ids.filtered('is_active').mapped('operator')
                operator_counts = {}
                for op in operators:
                    operator_counts[op] = operator_counts.get(op, 0) + 1
                
                operator_info = []
                for op, count in operator_counts.items():
                    operator_name = dict(self.env['contract.mobile.service']._fields['operator'].selection).get(op, op)
                    #operator_info.append(f"{operator_name}: {count}")
                
                #if operator_info:
                #    name += _(" (%s)") % ", ".join(operator_info)
            else:
                # If no active mobile services, don't invoice this line
                quantity = 0
        
        return {
            "quantity": quantity,
            "product_uom_id": self.uom_id.id,
            "discount": self.discount,
            "contract_line_id": self.id,
            "analytic_distribution": self.analytic_distribution,
            "sequence": self.sequence,
            "name": name,
            "price_unit": price_unit,
            "display_type": self.display_type or "product",
            "product_id": self.product_id.id,
        }

    def _get_period_to_invoice(
        self, last_date_invoiced, recurring_next_date, stop_at_date_end=True
    ):
        # TODO this method can now be removed, since
        # TODO self.next_period_date_start/end have the same values
        self.ensure_one()
        if not recurring_next_date:
            return False, False, False
        first_date_invoiced = (
            last_date_invoiced + relativedelta(days=1)
            if last_date_invoiced
            else self.date_start
        )
        last_date_invoiced = self.get_next_period_date_end(
            first_date_invoiced,
            self.recurring_rule_type,
            self.recurring_interval,
            max_date_end=(self.date_end if stop_at_date_end else False),
            next_invoice_date=recurring_next_date,
            recurring_invoicing_type=self.recurring_invoicing_type,
            recurring_invoicing_offset=self.recurring_invoicing_offset,
        )
        return first_date_invoiced, last_date_invoiced, recurring_next_date

    def _translate_marker_month_name(self, month_name):
        months = {
            "01": _("January"),
            "02": _("February"),
            "03": _("March"),
            "04": _("April"),
            "05": _("May"),
            "06": _("June"),
            "07": _("July"),
            "08": _("August"),
            "09": _("September"),
            "10": _("October"),
            "11": _("November"),
            "12": _("December"),
        }
        return months[month_name]

    def _insert_markers(self, first_date_invoiced, last_date_invoiced):
        self.ensure_one()
        lang_obj = self.env["res.lang"]
        lang = lang_obj.search([("code", "=", self.contract_id.partner_id.lang)])
        date_format = lang.date_format or "%m/%d/%Y"
        name = self.name
        name = name.replace("#START#", first_date_invoiced.strftime(date_format))
        name = name.replace("#END#", last_date_invoiced.strftime(date_format))
        name = name.replace(
            "#INVOICEMONTHNAME#",
            self.with_context(lang=lang.code)._translate_marker_month_name(
                first_date_invoiced.strftime("%m")
            ),
        )
        return name

    def _update_recurring_next_date(self):
        for rec in self:
            # 1. Compute the period just invoiced
            if rec.last_date_invoiced:
                period_start = rec.last_date_invoiced + relativedelta(days=1)
            else:
                period_start = rec.date_start

            period_end = rec.get_next_period_date_end(
                period_start,
                rec.recurring_rule_type,
                rec.recurring_interval,
                max_date_end=rec.date_end,
                next_invoice_date=rec.recurring_next_date,
                recurring_invoicing_type=rec.recurring_invoicing_type,
                recurring_invoicing_offset=rec.recurring_invoicing_offset,
            )

            # 2. Set last_date_invoiced to the end of the period just invoiced
            rec.last_date_invoiced = period_end

            # 3. Compute the next period start
            next_period_start = period_end + relativedelta(days=1) if period_end else False

            # 4. Compute the next recurring date from the next period start
            if next_period_start:
                next_recurring_date = rec.get_next_invoice_date(
                    next_period_start,
                    rec.recurring_invoicing_type,
                    rec.recurring_invoicing_offset,
                    rec.recurring_rule_type,
                    rec.recurring_interval,
                    max_date_end=rec.date_end,
                )
            else:
                next_recurring_date = False

            rec.recurring_next_date = next_recurring_date

    def _delay(self, delay_delta):
        """
        Delay a contract line
        :param delay_delta: delay relative delta
        :return: delayed contract line
        """
        for rec in self:
            if rec.last_date_invoiced:
                raise ValidationError(
                    _("You can't delay a contract line " "invoiced at least one time.")
                )
            new_date_start = rec.date_start + delay_delta
            if rec.date_end:
                new_date_end = rec.date_end + delay_delta
            else:
                new_date_end = False
            new_recurring_next_date = self.get_next_invoice_date(
                new_date_start,
                rec.recurring_invoicing_type,
                rec.recurring_invoicing_offset,
                rec.recurring_rule_type,
                rec.recurring_interval,
                max_date_end=new_date_end,
            )
            rec.write(
                {
                    "date_start": new_date_start,
                    "date_end": new_date_end,
                    "recurring_next_date": new_recurring_next_date,
                }
            )

    def _prepare_value_for_stop(self, date_end, manual_renew_needed):
        self.ensure_one()
        return {
            "date_end": date_end,
            "is_auto_renew": False,
            "manual_renew_needed": manual_renew_needed,
            "recurring_next_date": self.get_next_invoice_date(
                self.next_period_date_start,
                self.recurring_invoicing_type,
                self.recurring_invoicing_offset,
                self.recurring_rule_type,
                self.recurring_interval,
                max_date_end=date_end,
            ),
        }

    def stop(self, date_end, manual_renew_needed=False, post_message=True):
        """
        Put date_end on contract line
        We don't consider contract lines that end's before the new end date
        :param date_end: new date end for contract line
        :return: True
        """
        if not all(self.mapped("is_stop_allowed")):
            raise ValidationError(_("Stop not allowed for this line"))
        for rec in self:
            if date_end < rec.date_start:
                rec.cancel()
            else:
                if not rec.date_end or rec.date_end > date_end:
                    old_date_end = rec.date_end
                    rec.write(
                        rec._prepare_value_for_stop(date_end, manual_renew_needed)
                    )
                    if post_message:
                        msg = _(
                            """Contract line for <strong>%(product)s</strong>
                            stopped: <br/>
                            - <strong>End</strong>: %(old_end)s -- %(new_end)s
                            """
                        ) % {
                            "product": rec.name,
                            "old_end": old_date_end,
                            "new_end": rec.date_end,
                        }
                        rec.contract_id.message_post(body=msg)
                else:
                    rec.write(
                        {
                            "is_auto_renew": False,
                            "manual_renew_needed": manual_renew_needed,
                        }
                    )
        return True

    def _prepare_value_for_plan_successor(
        self, date_start, date_end, is_auto_renew, recurring_next_date=False
    ):
        self.ensure_one()
        if not recurring_next_date:
            recurring_next_date = self.get_next_invoice_date(
                date_start,
                self.recurring_invoicing_type,
                self.recurring_invoicing_offset,
                self.recurring_rule_type,
                self.recurring_interval,
                max_date_end=date_end,
            )
        new_vals = self.read()[0]
        new_vals.pop("id", None)
        new_vals.pop("last_date_invoiced", None)
        values = self._convert_to_write(new_vals)
        values["date_start"] = date_start
        values["date_end"] = date_end
        values["recurring_next_date"] = recurring_next_date
        values["is_auto_renew"] = is_auto_renew
        values["predecessor_contract_line_id"] = self.id
        return values

    def plan_successor(
        self,
        date_start,
        date_end,
        is_auto_renew,
        recurring_next_date=False,
        post_message=True,
    ):
        """
        Create a copy of a contract line in a new interval
        :param date_start: date_start for the successor_contract_line
        :param date_end: date_end for the successor_contract_line
        :param is_auto_renew: is_auto_renew option for successor_contract_line
        :param recurring_next_date: recurring_next_date for the
        successor_contract_line
        :return: successor_contract_line
        """
        contract_line = self.env["contract.line"]
        for rec in self:
            if not rec.is_plan_successor_allowed:
                raise ValidationError(_("Plan successor not allowed for this line"))
            rec.is_auto_renew = False
            new_line = self.create(
                rec._prepare_value_for_plan_successor(
                    date_start, date_end, is_auto_renew, recurring_next_date
                )
            )
            rec.successor_contract_line_id = new_line
            contract_line |= new_line
            if post_message:
                msg = _(
                    """Contract line for <strong>%(product)s</strong>
                    planned a successor: <br/>
                    - <strong>Start</strong>: %(new_date_start)s
                    <br/>
                    - <strong>End</strong>: %(new_date_end)s
                    """
                ) % {
                    "product": rec.name,
                    "new_date_start": new_line.date_start,
                    "new_date_end": new_line.date_end,
                }
                rec.contract_id.message_post(body=msg)
        return contract_line

    def stop_plan_successor(self, date_start, date_end, is_auto_renew):
        """
        Stop a contract line for a defined period and start it later
        Cases to consider:
            * contract line end's before the suspension period:
                -> apply stop
            * contract line start before the suspension period and end in it
                -> apply stop at suspension start date
                -> apply plan successor:
                    - date_start: suspension.date_end
                    - date_end: date_end    + (contract_line.date_end
                                            - suspension.date_start)
            * contract line start before the suspension period and end after it
                -> apply stop at suspension start date
                -> apply plan successor:
                    - date_start: suspension.date_end
                    - date_end: date_end + (suspension.date_end
                                        - suspension.date_start)
            * contract line start and end's in the suspension period
                -> apply delay
                    - delay: suspension.date_end - contract_line.date_start
            * contract line start in the suspension period and end after it
                -> apply delay:
                    - delay: suspension.date_end - contract_line.date_start
            * contract line start  and end after the suspension period
                -> apply delay:
                    - delay: suspension.date_end - suspension.start_date
        :param date_start: suspension start date
        :param date_end: suspension end date
        :param is_auto_renew: is the new line is set to auto_renew
        :return: created contract line
        """
        if not all(self.mapped("is_stop_plan_successor_allowed")):
            raise ValidationError(_("Stop/Plan successor not allowed for this line"))
        contract_line = self.env["contract.line"]
        for rec in self:
            if rec.date_start >= date_start:
                if rec.date_start < date_end:
                    delay = (date_end - rec.date_start) + timedelta(days=1)
                else:
                    delay = (date_end - date_start) + timedelta(days=1)
                rec._delay(delay)
                contract_line |= rec
            else:
                if rec.date_end and rec.date_end < date_start:
                    rec.stop(date_start, post_message=False)
                elif (
                    rec.date_end
                    and rec.date_end > date_start
                    and rec.date_end < date_end
                ):
                    new_date_start = date_end + relativedelta(days=1)
                    new_date_end = (
                        date_end + (rec.date_end - date_start) + relativedelta(days=1)
                    )
                    rec.stop(
                        date_start - relativedelta(days=1),
                        manual_renew_needed=True,
                        post_message=False,
                    )
                    contract_line |= rec.plan_successor(
                        new_date_start,
                        new_date_end,
                        is_auto_renew,
                        post_message=False,
                    )
                else:
                    new_date_start = date_end + relativedelta(days=1)
                    if rec.date_end:
                        new_date_end = (
                            rec.date_end
                            + (date_end - date_start)
                            + relativedelta(days=1)
                        )
                    else:
                        new_date_end = rec.date_end

                    rec.stop(
                        date_start - relativedelta(days=1),
                        manual_renew_needed=True,
                        post_message=False,
                    )
                    contract_line |= rec.plan_successor(
                        new_date_start,
                        new_date_end,
                        is_auto_renew,
                        post_message=False,
                    )
            msg = _(
                """Contract line for <strong>%(product)s</strong>
                suspended: <br/>
                - <strong>Suspension Start</strong>: %(new_date_start)s
                <br/>
                - <strong>Suspension End</strong>: %(new_date_end)s
                """
            ) % {
                "product": rec.name,
                "new_date_start": date_start,
                "new_date_end": date_end,
            }
            rec.contract_id.message_post(body=msg)
        return contract_line

    def cancel(self):
        if not all(self.mapped("is_cancel_allowed")):
            raise ValidationError(_("Cancel not allowed for this line"))
        for contract in self.mapped("contract_id"):
            lines = self.filtered(lambda line, c=contract: line.contract_id == c)
            msg = _(
                "Contract line canceled: %s",
                "<br/>- ".join(
                    [f"<strong>{name}</strong>" for name in lines.mapped("name")]
                ),
            )
            contract.message_post(body=msg)
        self.mapped("predecessor_contract_line_id").write(
            {"successor_contract_line_id": False}
        )
        return self.write({"is_canceled": True, "is_auto_renew": False})

    def uncancel(self, recurring_next_date):
        if not all(self.mapped("is_un_cancel_allowed")):
            raise ValidationError(_("Un-cancel not allowed for this line"))
        for contract in self.mapped("contract_id"):
            lines = self.filtered(lambda line, c=contract: line.contract_id == c)
            msg = _(
                "Contract line Un-canceled: %s",
                "<br/>- ".join(
                    [f"<strong>{name}</strong>" for name in lines.mapped("name")]
                ),
            )
            contract.message_post(body=msg)
        for rec in self:
            if rec.predecessor_contract_line_id:
                predecessor_contract_line = rec.predecessor_contract_line_id
                assert not predecessor_contract_line.successor_contract_line_id
                predecessor_contract_line.successor_contract_line_id = rec
            rec.is_canceled = False
            rec.recurring_next_date = recurring_next_date
        return True

    def action_uncancel(self):
        self.ensure_one()
        context = {
            "default_contract_line_id": self.id,
            "default_recurring_next_date": fields.Date.context_today(self),
        }
        context.update(self.env.context)
        view_id = self.env.ref("contract.contract_line_wizard_uncancel_form_view").id
        return {
            "type": "ir.actions.act_window",
            "name": "Un-Cancel Contract Line",
            "res_model": "contract.line.wizard",
            "view_mode": "form",
            "views": [(view_id, "form")],
            "target": "new",
            "context": context,
        }

    def action_plan_successor(self):
        self.ensure_one()
        context = {
            "default_contract_line_id": self.id,
            "default_is_auto_renew": self.is_auto_renew,
        }
        context.update(self.env.context)
        view_id = self.env.ref(
            "contract.contract_line_wizard_plan_successor_form_view"
        ).id
        return {
            "type": "ir.actions.act_window",
            "name": "Plan contract line successor",
            "res_model": "contract.line.wizard",
            "view_mode": "form",
            "views": [(view_id, "form")],
            "target": "new",
            "context": context,
        }

    def action_stop(self):
        self.ensure_one()
        context = {
            "default_contract_line_id": self.id,
            "default_date_end": self.date_end,
        }
        context.update(self.env.context)
        view_id = self.env.ref("contract.contract_line_wizard_stop_form_view").id
        return {
            "type": "ir.actions.act_window",
            "name": "Terminate contract line",
            "res_model": "contract.line.wizard",
            "view_mode": "form",
            "views": [(view_id, "form")],
            "target": "new",
            "context": context,
        }

    def action_stop_plan_successor(self):
        self.ensure_one()
        context = {
            "default_contract_line_id": self.id,
            "default_is_auto_renew": self.is_auto_renew,
        }
        context.update(self.env.context)
        view_id = self.env.ref(
            "contract.contract_line_wizard_stop_plan_successor_form_view"
        ).id
        return {
            "type": "ir.actions.act_window",
            "name": "Suspend contract line",
            "res_model": "contract.line.wizard",
            "view_mode": "form",
            "views": [(view_id, "form")],
            "target": "new",
            "context": context,
        }

    def _get_renewal_new_date_end(self):
        self.ensure_one()
        date_start = self.date_end + relativedelta(days=1)
        date_end = self._get_first_date_end(
            date_start, self.auto_renew_rule_type, self.auto_renew_interval
        )
        return date_end

    def _renew_create_line(self, date_end):
        self.ensure_one()
        date_start = self.date_end + relativedelta(days=1)
        is_auto_renew = self.is_auto_renew
        self.stop(self.date_end, post_message=False)
        new_line = self.plan_successor(
            date_start, date_end, is_auto_renew, post_message=False
        )
        return new_line

    def _renew_extend_line(self, date_end):
        self.ensure_one()
        self.date_end = date_end
        return self

    def renew(self):
        res = self.env["contract.line"]
        for rec in self:
            company = rec.contract_id.company_id
            date_end = rec._get_renewal_new_date_end()
            date_start = rec.date_end + relativedelta(days=1)
            if company.create_new_line_at_contract_line_renew:
                new_line = rec._renew_create_line(date_end)
            else:
                new_line = rec._renew_extend_line(date_end)
            res |= new_line
            msg = _(
                """Contract line for <strong>%(product)s</strong>
                renewed: <br/>
                - <strong>Start</strong>: %(new_date_start)s
                <br/>
                - <strong>End</strong>: %(new_date_end)s
                """
            ) % {
                "product": rec.name,
                "new_date_start": date_start,
                "new_date_end": date_end,
            }
            rec.contract_id.message_post(body=msg)
        return res

    @api.model
    def _contract_line_to_renew_domain(self):
        return [
            ("contract_id.is_terminated", "=", False),
            ("is_auto_renew", "=", True),
            ("is_canceled", "=", False),
            ("termination_notice_date", "<=", fields.Date.context_today(self)),
        ]

    @api.model
    def cron_renew_contract_line(self):
        domain = self._contract_line_to_renew_domain()
        to_renew = self.search(domain)
        to_renew.renew()

    @api.model
    def get_view(self, view_id=None, view_type="form", **options):
        default_contract_type = self.env.context.get("default_contract_type")
        if view_type == "tree" and default_contract_type == "purchase":
            view_id = self.env.ref("contract.contract_line_supplier_tree_view").id
        if view_type == "form":
            if default_contract_type == "purchase":
                view_id = self.env.ref("contract.contract_line_supplier_form_view").id
            elif default_contract_type == "sale":
                view_id = self.env.ref("contract.contract_line_customer_form_view").id
        return super().get_view(view_id, view_type, **options)

    def unlink(self):
        """Allow deletion of any contract line regardless of cancellation status
        and delete associated mobile services if any"""
        # First delete mobile services if this is a mobile service line
        for line in self:
            if line.is_mobile_service and line.mobile_service_ids:
                line.mobile_service_ids.unlink()
                
        return super().unlink()

    def _get_quantity_to_invoice(
        self, period_first_date, period_last_date, invoice_date
    ):
        self.ensure_one()
        return self.quantity if not self.display_type else 0.0
        
    @api.depends('inventory_line_ids.quantity')
    def _compute_inventory_quantity(self):
        for line in self:
            line.inventory_quantity = sum(line.inventory_line_ids.mapped('quantity'))
            
    @api.depends('in_inventory', 'quantity', 'inventory_quantity')
    def _compute_inventory_state(self):
        for line in self:
            if not line.in_inventory:
                line.inventory_state = 'not_tracked'
            elif line.inventory_quantity <= 0:
                line.inventory_state = 'pending'
            elif line.inventory_quantity >= line.quantity:
                line.inventory_state = 'allocated'
            else:
                line.inventory_state = 'partial'
                
    @api.onchange('in_inventory')
    def _onchange_in_inventory(self):
        if self.in_inventory and not self.contract_id.inventory_id:
            return {
                'warning': {
                    'title': _('No Inventory Selected'),
                    'message': _('Please select an inventory storage in the contract form.')
                }
            }
            
    def update_inventory(self):
        """Create or update inventory lines based on contract lines"""
        for line in self.filtered('in_inventory'):
            if not line.contract_id.inventory_id:
                continue
                
            # Check if an inventory line already exists
            existing_line = self.env['contract.inventory.line'].search([
                ('contract_line_id', '=', line.id),
                ('product_id', '=', line.product_id.id),
            ], limit=1)
            
            if existing_line:
                # If quantity has changed, update the inventory line
                if existing_line.quantity != line.quantity:
                    existing_line.write({'quantity': line.quantity})
            else:
                # Create a new inventory line
                self.env['contract.inventory.line'].create({
                    'inventory_id': line.contract_id.inventory_id.id,
                    'product_id': line.product_id.id,
                    'contract_line_id': line.id,
                    'quantity': line.quantity,
                    'state': 'assigned',
                })
                
    def remove_from_inventory(self):
        """Remove products from inventory when contract line is removed or marked as not in inventory"""
        for line in self:
            inventory_lines = self.env['contract.inventory.line'].search([
                ('contract_line_id', '=', line.id),
            ])
            if inventory_lines:
                inventory_lines.write({'state': 'returned'})
                
    def write(self, vals):
        # Prevent propagation of recurring_next_date changes if they come from a single line edit
        skip_recompute = self.env.context.get('no_next_period_recompute', False)
        skip_contract_update = len(self) == 1 and 'recurring_next_date' in vals
        
        if skip_contract_update:
            self = self.with_context(no_contract_next_date_update=True)
            
        # If we're updating specific fields that shouldn't trigger recomputation
        if any(field in vals for field in [
            'name', 'quantity', 'price_unit', 'discount',
            'display_type', 'sequence', 'is_canceled',
            'in_inventory', 'is_mobile_service'
        ]):
            skip_recompute = True
            
        # If we're explicitly changing date-related fields, allow recomputation
        if any(field in vals for field in [
            'date_start', 'date_end', 'recurring_next_date',
            'last_date_invoiced', 'recurring_rule_type',
            'recurring_interval', 'recurring_invoicing_type',
            'recurring_invoicing_offset'
        ]):
            skip_recompute = False
            
        if skip_recompute:
            self = self.with_context(no_next_period_recompute=True)
            
        result = super().write(vals)
        
        # Handle inventory changes
        if 'in_inventory' in vals:
            for line in self:
                if vals['in_inventory']:
                    line.update_inventory()
                else:
                    line.remove_from_inventory()
        elif 'product_id' in vals or 'quantity' in vals:
            self.filtered('in_inventory').update_inventory()
        
        # Handle mobile service changes
        if 'is_mobile_service' in vals:
            for line in self:
                if vals['is_mobile_service']:
                    line.update_mobile_services_inventory()
                else:
                    line.remove_mobile_services_from_inventory()
        
        # Skip name update if we're already doing it from mobile service update
        # or if 'name' was explicitly passed in vals (direct user update)
        if not self.env.context.get('skip_mobile_service_description_update') and 'name' not in vals:
            mobile_service_lines = self.filtered('is_mobile_service')
            if mobile_service_lines:
                mobile_service_lines._compute_mobile_service_description()
        
        return result

    @api.depends('mobile_service_ids')
    def _compute_mobile_service_count(self):
        for line in self:
            line.mobile_service_count = len(line.mobile_service_ids)
            
    def update_mobile_services_inventory(self):
        """Create or update mobile services in inventory based on contract lines"""
        for line in self.filtered('is_mobile_service'):
            if not line.contract_id.inventory_id:
                continue
                
            # If there are no mobile services yet, create a default one
            if not line.mobile_service_ids:
                mobile_service = self.env['contract.mobile.service'].create({
                    'name': line.product_id.name or "Mobile Service",
                    'phone_number': _('New Phone Number'),
                    'operator': 'telekom',  # Default operator
                    'is_active': True,
                    'inventory_id': line.contract_id.inventory_id.id,
                    'contract_line_id': line.id,
                })
    
    def remove_mobile_services_from_inventory(self):
        """Mark mobile services as inactive when contract line is removed or not tracked"""
        for line in self:
            if line.mobile_service_ids:
                line.mobile_service_ids.write({'is_active': False})
    
    def action_view_mobile_services(self):
        """Open the mobile services related to this contract line"""
        self.ensure_one()
        action = self.env.ref('contract.action_contract_mobile_service').read()[0]
        
        # Make the button/link description more informative
        if self.mobile_service_ids:
            action['name'] = _('Mobile Services: %s') % ", ".join(self.mobile_service_ids.mapped('phone_number'))
        
        action.update({
            'domain': [('contract_line_id', '=', self.id)],
            'context': {
                'default_contract_line_id': self.id,
                'default_inventory_id': self.contract_id.inventory_id.id,
                'default_name': self.name or self.product_id.name,
            },
        })
        return action

    @api.depends('mobile_service_ids', 'mobile_service_ids.phone_number', 'mobile_service_ids.is_active')
    def _compute_mobile_service_description(self):
        """Update the description of the contract line based on mobile service phone numbers"""
        # Check if we're already in a mobile service update to prevent recursion
        if self.env.context.get('skip_mobile_service_description_update'):
            return
            
        for line in self:
            if line.is_mobile_service and line.mobile_service_ids:
                # Get all active mobile services phone numbers
                active_services = line.mobile_service_ids.filtered('is_active')
                if active_services:
                    # Get all phone numbers as a comma-separated string
                    phone_numbers = ", ".join(active_services.mapped('phone_number'))
                    # Update the name with the product name and phone numbers
                    product_name = line.product_id.name or "Mobile Service"
                    # Use with_context to avoid triggering recursion
                    line.with_context(skip_mobile_service_description_update=True).name = f"{product_name}: {phone_numbers}"

    @api.model
    def create(self, vals):
        # If x_datum_viazanosti_produktu is not set, inherit from contract
        if not vals.get('x_datum_viazanosti_produktu') and vals.get('contract_id'):
            contract = self.env['contract.contract'].browse(vals['contract_id'])
            # Try to get x_datum_viazanosti from contract, fallback to None
            x_datum_viazanosti = getattr(contract, 'x_datum_viazanost', None)
            if x_datum_viazanosti:
                vals['x_datum_viazanosti_produktu'] = x_datum_viazanosti
        # If recurring_next_date is not set, inherit from contract
        if not vals.get('recurring_next_date') and vals.get('contract_id'):
            contract = self.env['contract.contract'].browse(vals['contract_id'])
            if contract and contract.recurring_next_date:
                vals['recurring_next_date'] = contract.recurring_next_date
        return super().create(vals)

    @api.model_create_multi
    def create(self, vals_list):
        return super(
            ContractLine,
            self.with_context(creating_contract_line=True)
        ).create(vals_list)


