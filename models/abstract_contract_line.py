# Copyright 2004-2010 OpenERP SA
# Copyright 2014 Angel Moya <angel.moya@domatix.com>
# Copyright 2015 Pedro M. Baeza <pedro.baeza@tecnativa.com>
# Copyright 2016-2018 Carlos Dauden <carlos.dauden@tecnativa.com>
# Copyright 2016-2017 LasLabs Inc.
# Copyright 2018 ACSONE SA/NV
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.translate import _


class ContractAbstractContractLine(models.AbstractModel):
    _inherit = "contract.recurrency.basic.mixin"
    _name = "contract.abstract.contract.line"
    _description = "Abstract Recurring Contract Line"

    product_id = fields.Many2one("product.product", string="Položka", domain="[('active', '=', True)]")
    name = fields.Text(string="Popis", required=True)
    partner_id = fields.Many2one(
        comodel_name="res.partner", related="contract_id.partner_id"
    )
    quantity = fields.Float(default=1.0, required=True)
    product_uom_category_id = fields.Many2one(  # Used for domain of field uom_id
        comodel_name="uom.category",
        related="product_id.uom_id.category_id",
    )
    uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="Jednotky",
        domain="[('category_id', '=', product_uom_category_id)]",
    )
    automatic_price = fields.Boolean(
        string="Zdediť cenu?",
        help="If this is marked, the price will be obtained automatically "
        "applying the pricelist to the product. If not, you will be "
        "able to introduce a manual price",
    )
    # Just to have a currency_id here - will get overwriten in contract.line
    # model with the related currency from the contract
    currency_id = fields.Many2one("res.currency")
    specific_price = fields.Float()
    price_unit = fields.Float(
        string="Jednotková cena",
        compute="_compute_price_unit",
        inverse="_inverse_price_unit",
    )
    price_subtotal_before_discount = fields.Float(
        string="Subtotal Before Discount",
        compute="_compute_price_subtotal",
        store=True,
    )
    price_subtotal = fields.Monetary(
        compute="_compute_price_subtotal",
        string="Súčet",
        #store=True,
    )
    discount = fields.Float(
        string="Zľava (%)",
        digits="Discount",
        help="Discount that is applied in generated invoices."
        " It should be less or equal to 100",
    )
    sequence = fields.Integer(
        default=10,
        help="Sequence of the contract line when displaying contracts",
    )
    recurring_rule_type = fields.Selection(
        compute="_compute_recurring_rule_type",
        store=True,
        readonly=False,
        required=True,
        copy=True,
    )
    recurring_invoicing_type = fields.Selection(
        compute="_compute_recurring_invoicing_type",
        store=True,
        readonly=False,
        required=True,
        copy=True,
    )
    recurring_interval = fields.Integer(
        compute="_compute_recurring_interval",
        store=True,
        readonly=False,
        required=True,
        copy=True,
    )
    date_start = fields.Date(
        compute="_compute_date_start",
        store=True,
        readonly=False,
        copy=True,
    )
    last_date_invoiced = fields.Date()
    is_canceled = fields.Boolean(string="Canceled", default=False)
    is_auto_renew = fields.Boolean(string="Auto Renew", default=False)
    auto_renew_interval = fields.Integer(
            default=1,
        string="Renew Every",
        help="Renew every (Days/Week/Month/Year)",
    )
    auto_renew_rule_type = fields.Selection(
        [
            ("daily", "Day(s)"),
            ("weekly", "Week(s)"),
            ("monthly", "Month(s)"),
            ("yearly", "Year(s)"),
        ],
        default="yearly",
        string="Renewal type",
        help="Specify Interval for automatic renewal.",
    )
    termination_notice_interval = fields.Integer(
        default=1, string="Termination Notice Before"
    )
    termination_notice_rule_type = fields.Selection(
        [("daily", "Day(s)"), ("weekly", "Week(s)"), ("monthly", "Month(s)")],
        default="monthly",
        string="Termination Notice type",
    )
    contract_id = fields.Many2one(
        string="Contract",
        comodel_name="contract.abstract.contract",
        required=True,
        ondelete="cascade",
    )
    display_type = fields.Selection(
        selection=[("line_section", "Section"), ("line_note", "Note")],
        default=False,
        help="Technical field for UX purpose.",
    )
    note_invoicing_mode = fields.Selection(
        selection=[
            ("with_previous_line", "With previous line"),
            ("with_next_line", "With next line"),
            ("custom", "Custom"),
        ],
        default="with_previous_line",
        help="Defines when the Note is invoiced:\n"
        "- With previous line: If the previous line can be invoiced.\n"
        "- With next line: If the next line can be invoiced.\n"
        "- Custom: Depending on the recurrence to be define.",
    )
    is_recurring_note = fields.Boolean(compute="_compute_is_recurring_note")
    company_id = fields.Many2one(related="contract_id.company_id", store=True)
    
    # Add the new fields
    commitment = fields.Selection(
        [
            ('none', 'No Commitment'),
            ('1_year', '1 Year'),
            ('2_years', '2 Years'),
        ],
        string="Viazanosť",
        default='none',
    )
    
    commitment_discount = fields.Float(
        string="Zľava z viazanosti",
        compute="_compute_commitment_discount",
        store=True,
    )

    x_zlavnena_cena = fields.Float(
        string="Zlavnena cena",
        help="Discounted price applied when within commitment date (Datum viazanosti)",
    )
    
    x_datum_viazanosti_produktu = fields.Date(
        string="Dátum viazanosti produktu",
        help="Commitment date for this specific product line",
    )

    def _set_recurrence_field(self, field):
        """Helper method for computed methods that gets the equivalent field
        in the header.

        We need to re-assign the original value for avoiding a missing error.
        """
        for record in self:
            if record.contract_id.line_recurrence:
                record[field] = record[field]
            else:
                record[field] = record.contract_id[field]

    @api.depends("contract_id.recurring_rule_type", "contract_id.line_recurrence")
    def _compute_recurring_rule_type(self):
        self._set_recurrence_field("recurring_rule_type")

    @api.depends("contract_id.recurring_invoicing_type", "contract_id.line_recurrence")
    def _compute_recurring_invoicing_type(self):
        self._set_recurrence_field("recurring_invoicing_type")

    @api.depends("contract_id.recurring_interval", "contract_id.line_recurrence")
    def _compute_recurring_interval(self):
        self._set_recurrence_field("recurring_interval")

    @api.depends("contract_id.date_start", "contract_id.line_recurrence")
    def _compute_date_start(self):
        self._set_recurrence_field("date_start")

    # pylint: disable=missing-return
    @api.depends("contract_id.recurring_next_date", "contract_id.line_recurrence")
    def _compute_recurring_next_date(self):
        super()._compute_recurring_next_date()
        self._set_recurrence_field("recurring_next_date")

    @api.depends("display_type", "note_invoicing_mode")
    def _compute_is_recurring_note(self):
        for record in self:
            record.is_recurring_note = (
                record.display_type == "line_note"
                and record.note_invoicing_mode == "custom"
            )

    @api.depends(
        "automatic_price",
        "specific_price",
        "product_id",
        "quantity",
        "contract_id.pricelist_id",
        "contract_id.partner_id",
    )
    def _compute_price_unit(self):
        """Get the specific price if no auto-price, and the price obtained
        from the pricelist otherwise. If the line name contains 'prenájom',
        use x_zlavnena_cena as the price unit.
        """
        for line in self:
            if line.name and "prenájom" in line.name.lower():
                line.price_unit = line.x_zlavnena_cena if line.x_zlavnena_cena else 0.0
            elif line.automatic_price and line.product_id:
                pricelist = (
                    line.contract_id.pricelist_id
                    or line.contract_id.partner_id.with_company(
                        line.contract_id.company_id
                    ).property_product_pricelist
                )
                product = line.product_id.with_context(
                    quantity=line.env.context.get(
                        "contract_line_qty",
                        line.quantity,
                    ),
                    pricelist=pricelist.id,
                    partner=line.contract_id.partner_id.id,
                    date=line.env.context.get(
                        "old_date", fields.Date.context_today(line)
                    ),
                )
                line.price_unit = pricelist._get_product_price(product, quantity=1)
            else:
                line.price_unit = line.specific_price

    # Tip in https://github.com/odoo/odoo/issues/23891#issuecomment-376910788
    @api.onchange("price_unit")
    def _inverse_price_unit(self):
        """Store the specific price in the no auto-price records."""
        for line in self.filtered(lambda x: not x.automatic_price):
            line.specific_price = line.price_unit

    @api.depends("quantity", "price_unit", "discount", "commitment_discount", "x_zlavnena_cena", "x_datum_viazanosti_produktu")
    def _compute_price_subtotal(self):
        today = fields.Date.context_today(self)
        for line in self:
            # Store the original subtotal before discount
            line.price_subtotal_before_discount = line.quantity * line.price_unit
            
            # Determine which price to use based on product-specific commitment date
            use_discounted_price = False
            if line.x_datum_viazanosti_produktu:
                if line.x_datum_viazanosti_produktu >= today:
                    use_discounted_price = True
            # Even if there's no commitment date but we have a discounted price, use it
            elif line.x_zlavnena_cena != 0:
                use_discounted_price = True
            
            # Apply the appropriate price
            if use_discounted_price:
                # Use the discounted price directly
                unit_price = line.x_zlavnena_cena
            else:
                # Apply the commitment discount to the regular price
                unit_price = line.price_unit - line.commitment_discount
            
            # Calculate subtotal with the selected price
            subtotal = line.quantity * unit_price
            
            # Apply the percentage discount if any
            discount = line.discount / 100
            subtotal *= 1 - discount
            
            # Apply currency rounding if needed
            if line.contract_id.pricelist_id:
                cur = line.contract_id.pricelist_id.currency_id
                line.price_subtotal = cur.round(subtotal)
            else:
                line.price_subtotal = subtotal

    @api.depends('x_zlavnena_cena', 'x_datum_viazanosti_produktu')
    def _compute_commitment_discount(self):
        today = fields.Date.context_today(self)
        for line in self:
            # Initialize discount to 0
            line.commitment_discount = 0.0
            
            # Check if product-specific commitment date exists and is valid
            if line.x_datum_viazanosti_produktu and line.x_datum_viazanosti_produktu >= today:
                # If within commitment date and we have a discounted price, calculate the commitment discount
                if line.x_zlavnena_cena != 0:
                    line.commitment_discount = max(0, line.price_unit - line.x_zlavnena_cena)
                # If no discounted price set specifically, use the old logic
                else:
                    if line.commitment == '1_year':
                        line.commitment_discount = 2.0
                    elif line.commitment == '2_years':
                        line.commitment_discount = 4.0

    @api.constrains("discount")
    def _check_discount(self):
        for line in self:
            if line.discount > 100:
                raise ValidationError(_("Discount should be less or equal to 100"))

    @api.onchange("product_id")
    def _onchange_product_id(self):
        vals = {}
        if not self.uom_id or (
            self.product_id.uom_id.category_id.id != self.uom_id.category_id.id
        ):
            vals["uom_id"] = self.product_id.uom_id

        date = self.recurring_next_date or fields.Date.context_today(self)
        partner = self.contract_id.partner_id or self.env.user.partner_id
        if self.product_id:
            product = self.product_id.with_context(
                lang=partner.lang,
                partner=partner.id,
                quantity=self.quantity,
                date=date,
                pricelist=self.contract_id.pricelist_id.id,
                uom=self.uom_id.id,
            )
            vals["name"] = self.product_id.get_product_multiline_description_sale()
            if self.contract_id.pricelist_id:
                vals["price_unit"] = self.contract_id.pricelist_id._get_product_price(
                    product, quantity=1
                )
            else:
                vals["price_unit"] = 0.0
        self.update(vals)

    def _prepare_invoice_line(self, move_form=False, **kwargs):
        """Prepare the values for creating an invoice line.

        This is where we need to apply the discounted price when within commitment date.
        """
        self.ensure_one()
        
        # Determine which price to use based on product-specific commitment date
        unit_price = self.price_unit
        today = fields.Date.context_today(self)
        
        if self.x_datum_viazanosti_produktu and self.x_datum_viazanosti_produktu >= today:
            # Within commitment date
            if self.x_zlavnena_cena != 0:
                # Use the directly specified discounted price (even if negative)
                unit_price = self.x_zlavnena_cena
            else:
                # Use the calculated commitment discount
                unit_price = self.price_unit - self.commitment_discount
        # Even if there's no commitment date but we have a discounted price, use it
        elif self.x_zlavnena_cena != 0:
            unit_price = self.x_zlavnena_cena
        
        # Filter taxes to only include those matching the contract's company
        company_id = self.contract_id.company_id.id if self.contract_id else False
        if company_id and self.product_id:
            tax_ids = self.product_id.taxes_id.filtered(
                lambda t: t.company_id.id == company_id
            ).ids
        else:
            tax_ids = self.product_id.taxes_id.ids if self.product_id else []
        
        res = {
            'display_type': self.display_type,
            'product_id': self.product_id.id,
            'name': self.name,
            'quantity': self.quantity,
            'price_unit': unit_price,  # Use the discounted price when applicable
            'discount': self.discount,
            'tax_ids': [(6, 0, tax_ids)],
            'analytic_distribution': self.contract_id.analytic_distribution or False,
        }
            
        if self.uom_id:
            res['product_uom_id'] = self.uom_id.id
        if move_form and res.get('price_unit', 0.0) != 0.0:
            res['product_id'] = self.product_id
        kwargs['contract_line'] = self
        # Take current description if needed
        if kwargs.get('contract_line_name', False) and self.display_type not in [
            'line_section',
            'line_note',
        ]:
            res['name'] = kwargs.get('contract_line_name')
        # Allow customizations by other modules
        self.contract_id._prepare_invoice_line(res, **kwargs)
        return res

    def _get_invoice_line_name(self):
        """Return the invoice line name for this contract line."""
        self.ensure_one()
        name = self.name
        today = fields.Date.context_today(self)
        
        # Check if we're within the product-specific commitment date
        if self.x_datum_viazanosti_produktu and self.x_datum_viazanosti_produktu >= today:
            # Show discounted price information
            if self.x_zlavnena_cena != 0:
                name = "{} (s viazanosťou do: {} - zlavnena cena: {} {})".format(
                    name,
                    self.x_datum_viazanosti_produktu.strftime('%d.%m.%Y'),
                    self.x_zlavnena_cena,
                    self.currency_id.symbol
                )
            elif self.commitment != 'none' and self.commitment_discount > 0:
                name = "{} (s viazanosťou: {} - zľava: {} {})".format(
                    name, 
                    dict(self._fields['commitment'].selection).get(self.commitment),
                    self.commitment_discount,
                    self.currency_id.symbol
                )
        # Show discounted price even if no commitment date
        elif self.x_zlavnena_cena != 0:
            name = "{} (zlavnena cena: {} {})".format(
                name,
                self.x_zlavnena_cena,
                self.currency_id.symbol
            )
        return name
