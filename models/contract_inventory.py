# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _


class ContractInventory(models.Model):
    _name = "contract.inventory"
    _description = "Contract Inventory Storage"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Name", required=True, copy=False, tracking=True)
    code = fields.Char(string="Code", copy=False, tracking=True)
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        tracking=True,
    )
    contract_ids = fields.One2many(
        comodel_name="contract.contract",
        inverse_name="inventory_id",
        string="Contracts",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company", 
        string="Company",
        default=lambda self: self.env.company,
    )
    note = fields.Text(string="Notes")
    inventory_line_ids = fields.One2many(
        comodel_name="contract.inventory.line",
        inverse_name="inventory_id",
        string="Inventory Lines",
    )
    total_products = fields.Integer(
        string="Total Products",
        compute="_compute_total_products",
        store=True,
    )

    @api.depends('inventory_line_ids', 'inventory_line_ids.quantity')
    def _compute_total_products(self):
        for record in self:
            record.total_products = sum(record.inventory_line_ids.mapped('quantity'))

    def name_get(self):
        result = []
        for inventory in self:
            name = inventory.name
            if inventory.code:
                name = '[%s] %s' % (inventory.code, name)
            result.append((inventory.id, name))
        return result


class ContractInventoryLine(models.Model):
    _name = "contract.inventory.line"
    _description = "Contract Inventory Line"
    _rec_name = "product_id"

    inventory_id = fields.Many2one(
        comodel_name="contract.inventory",
        string="Inventory",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
        required=True,
    )
    contract_line_id = fields.Many2one(
        comodel_name="contract.line",
        string="Contract Line",
    )
    quantity = fields.Float(
        string="Quantity",
        default=1.0,
        required=True,
    )
    uom_id = fields.Many2one(
        related="product_id.uom_id",
        string="Unit of Measure",
    )
    date_added = fields.Date(
        string="Date Added",
        default=fields.Date.context_today,
    )
    state = fields.Selection(
        selection=[
            ('available', 'Available'),
            ('assigned', 'Assigned'),
            ('returned', 'Returned'),
        ],
        string="Status",
        default='available',
        tracking=True,
    )
    contract_id = fields.Many2one(
        related="contract_line_id.contract_id",
        string="Contract",
        store=True,
    )
    serial_number = fields.Char(
        string="Serial Number",
    )
    note = fields.Text(string="Notes")

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.uom_id = self.product_id.uom_id.id
