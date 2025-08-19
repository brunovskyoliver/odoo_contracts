# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from datetime import datetime


class ContractInventory(models.Model):
    _name = "contract.inventory"
    _description = "Contract Inventory Storage"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Name", required=True, copy=False, tracking=True)
    code = fields.Char(string="Code", copy=False, tracking=True)
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Default Warehouse',
        help='Default warehouse for stock operations',
        tracking=True,
    )
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
    picking_ids = fields.Many2many(
        'stock.picking',
        string="Related Stock Pickings",
        compute='_compute_picking_ids',
        store=True,
    )
    picking_count = fields.Integer(
        compute='_compute_picking_ids',
        store=True,
        string="Picking Count",
    )
    stock_state = fields.Selection([
        ('pending', 'Pending'),
        ('partial', 'Partially Processed'),
        ('done', 'Fully Processed'),
        ('cancelled', 'Cancelled'),
    ], string="Stock Status", compute='_compute_stock_state', store=True)

    @api.depends('inventory_line_ids', 'inventory_line_ids.quantity')
    def _compute_total_products(self):
        for record in self:
            record.total_products = sum(record.inventory_line_ids.mapped('quantity'))

    @api.depends('inventory_line_ids.stock_move_ids.picking_id')
    def _compute_picking_ids(self):
        for record in self:
            all_moves = self.env['stock.move'].search([
                ('contract_inventory_line_id', 'in', record.inventory_line_ids.ids)
            ])
            pickings = all_moves.mapped('picking_id')
            record.picking_ids = pickings
            record.picking_count = len(pickings)

    @api.depends('picking_ids', 'picking_ids.state')
    def _compute_stock_state(self):
        for record in self:
            if not record.picking_ids:
                record.stock_state = 'pending'
                continue
            
            states = record.picking_ids.mapped('state')
            if all(state == 'done' for state in states):
                record.stock_state = 'done'
            elif all(state == 'cancel' for state in states):
                record.stock_state = 'cancelled'
            elif any(state in ['assigned', 'done'] for state in states):
                record.stock_state = 'partial'
            else:
                record.stock_state = 'pending'

    def action_view_pickings(self):
        """Show related pickings"""
        self.ensure_one()
        return {
            'name': _('Stock Operations'),
            'view_mode': 'list,form',
            'res_model': 'stock.picking',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.picking_ids.ids)],
            'context': {
                'create': False,
                'default_picking_type_code': 'outgoing',
            },
        }

    def action_process_stock(self):
        """Process stock movements for all pending lines"""
        self.ensure_one()
        if not self.warehouse_id:
            raise UserError(_("Please select a warehouse first"))

        pending_lines = self.inventory_line_ids.filtered(lambda l: l.state == 'draft')
        if not pending_lines:
            raise UserError(_("No pending lines to process"))

        for line in pending_lines:
            line.process_stock_movement()

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

    def unlink(self):
        return super().unlink()
    _rec_name = "product_id"
    _inherit = ['mail.thread']

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
        domain="[('qty_available', '>', 0)]",  # Only show products with stock available
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
            ('draft', 'Draft'),
            ('pending', 'Pending'),
            ('assigned', 'Assigned'),
            ('returned', 'Returned'),
        ],
        string="Status",
        default='draft',
        tracking=True,
    )
    stock_move_ids = fields.One2many(
        'stock.move',
        'contract_inventory_line_id',
        string="Stock Moves",
        readonly=True,
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Warehouse',
        help='Specific warehouse to move this item to/from.',
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

    @api.model
    def create(self, vals):
        res = super().create(vals)
        if res.state != 'returned':  # Don't create stock move for returned items
            res.process_stock_movement()
        return res

    def process_stock_movement(self):
        """Process stock movement for this line"""
        self.ensure_one()
        
        quantity = self.env.context.get('quantity', self.quantity)

        if not self.warehouse_id and not self.inventory_id.warehouse_id:
            raise UserError(_("Please select a warehouse for the line or inventory"))

        warehouse = self.warehouse_id or self.inventory_id.warehouse_id
        
        # Determine locations based on operation type
        if self.state == 'returned':
            picking_type = warehouse.in_type_id
            location_src = self.env.ref('stock.stock_location_customers')
            location_dest = warehouse.lot_stock_id
        else:
            picking_type = warehouse.out_type_id
            location_src = warehouse.lot_stock_id
            location_dest = self.env.ref('stock.stock_location_customers')

        # Create or get picking
        domain = [
            ('picking_type_id', '=', picking_type.id),
            ('state', 'in', ['draft', 'assigned', 'confirmed']),
            ('partner_id', '=', self.contract_id.partner_id.id),
            ('location_id', '=', location_src.id),
            ('location_dest_id', '=', location_dest.id),
        ]
        picking = self.env['stock.picking'].search(domain, limit=1)
        
        if not picking:
            picking = self.env['stock.picking'].create({
                'picking_type_id': picking_type.id,
                'location_id': location_src.id,
                'location_dest_id': location_dest.id,
                'partner_id': self.contract_id.partner_id.id,
                'origin': f'Contract Inventory {self.inventory_id.name}',
                'company_id': self.inventory_id.company_id.id,
            })

        # Create stock move
        move_vals = {
            'name': f'{self.product_id.name} - {self.inventory_id.name}',
            'product_id': self.product_id.id,
            'product_uom_qty': quantity,
            'product_uom': self.uom_id.id,
            'picking_id': picking.id,
            'location_id': location_src.id,
            'location_dest_id': location_dest.id,
            'company_id': self.inventory_id.company_id.id,
            'picking_type_id': picking_type.id,
            'contract_inventory_line_id': self.id,
            'origin': f'Contract Inventory {self.inventory_id.name}',
        }

        # Create the move
        move = self.env['stock.move'].create(move_vals)

        # Confirm the picking
        picking.action_confirm()

        # Process the picking as immediate transfer
        for move in picking.move_ids_without_package:
            # Create a move line for the quantity
            self.env['stock.move.line'].create({
                'move_id': move.id,
                'picking_id': picking.id,
                'product_id': move.product_id.id,
                'product_uom_id': move.product_uom.id,
                'qty_done': quantity,
                'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id,
            })

        # Validate the picking
        picking.with_context(skip_backorder=True).button_validate()
        
        # Update line state
        self.write({'state': 'assigned'})
        
        return move

    def _return_to_stock(self, qty):
        """Create return stock move for the given quantity"""
        # Create a picking first
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.env.ref('stock.picking_type_in').id,
            'location_id': self.env.ref('stock.stock_location_customers').id,
            'location_dest_id': self.env.ref('stock.stock_location_stock').id,
            'partner_id': self.contract_id.partner_id.id,
            'origin': f'Contract Inventory Return {self.inventory_id.name}',
            'company_id': self.inventory_id.company_id.id,
        })

        # Create stock move
        stock_move = self.env['stock.move'].create({
            'name': _('Contract Inventory Return: %s') % self.inventory_id.name,
            'product_id': self.product_id.id,
            'product_uom_qty': qty,
            'product_uom': self.uom_id.id,
            'picking_id': picking.id,
            'location_id': self.env.ref('stock.stock_location_customers').id,
            'location_dest_id': self.env.ref('stock.stock_location_stock').id,
            'company_id': self.inventory_id.company_id.id,
            'picking_type_id': self.env.ref('stock.picking_type_in').id,
            'origin': f'Contract Inventory Return {self.inventory_id.name}',
        })

        # Confirm the picking
        picking.action_confirm()

        # Process the picking as immediate transfer
        for move in picking.move_ids_without_package:
            # Create a move line for the quantity
            self.env['stock.move.line'].create({
                'move_id': move.id,
                'picking_id': picking.id,
                'product_id': move.product_id.id,
                'product_uom_id': move.product_uom.id,
                'qty_done': qty,
                'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id,
            })

        # Validate the picking
        picking.with_context(skip_backorder=True).button_validate()
        return stock_move

    @api.model
    def create(self, vals):
        res = super().create(vals)
        if res.state != 'returned':  # Don't create stock move for returned items
            res.process_stock_movement()
        return res

    def write(self, vals):
        for record in self:
            old_qty = record.quantity
            old_state = record.state
            
            res = super().write(vals)
            
            if 'quantity' in vals or 'state' in vals:
                new_qty = vals.get('quantity', old_qty)
                new_state = vals.get('state', old_state)
                
                # Handle quantity changes
                if new_qty != old_qty and new_state != 'returned':
                    if new_qty > old_qty:
                        # Additional quantity needed
                        self.with_context(quantity=new_qty - old_qty).process_stock_movement()
                    else:
                        # Return excess quantity to stock
                        self.write({'state': 'returned'})
                        self.with_context(quantity=old_qty - new_qty).process_stock_movement()
                
                # Handle state changes
                if old_state != 'returned' and new_state == 'returned':
                    # Product is being returned
                    self.with_context(quantity=old_qty).process_stock_movement()
                elif old_state == 'returned' and new_state != 'returned':
                    # Product is being reactivated
                    self.with_context(quantity=new_qty).process_stock_movement()
        
        return res

    @api.constrains('quantity', 'product_id')
    def _check_quantity(self):
        for line in self:
            available_qty = line.product_id.with_context(location=self.env.ref('stock.stock_location_stock').id).qty_available
            if line.quantity > available_qty:
                raise ValidationError(_(
                    "Cannot assign more quantity than available in stock. "
                    "Product %s has only %s units available."
                ) % (line.product_id.name, line.product_id.qty_available))
