# Copyright 2016 Tecnativa - Carlos Dauden
# Copyright 2018 ACSONE SA/NV.
# Copyright 2020 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models, api, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    # We keep this field for migration purpose
    old_contract_id = fields.Many2one("contract.contract")

    # Add default for taxable_supply_date
    taxable_supply_date = fields.Date(
        string='Dátum zdanitelného plnenia',
        default=fields.Date.context_today,
    )

    # Stock integration fields
    has_stock_moves = fields.Boolean(
        string="Has Stock Moves",
        compute='_compute_has_stock_moves',
        store=True,
    )
    stock_move_ids = fields.One2many(
        'stock.move',
        'invoice_line_id',
        string="Stock Moves",
        readonly=True,
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
        ('partial', 'Partially Received'),
        ('done', 'Fully Received'),
        ('cancelled', 'Cancelled'),
        ('no_stock', 'No Stock Required'),
    ], string="Stock Status", compute='_compute_stock_state', store=True)

    def action_create_stock_moves(self):
        """Create stock moves for invoice lines to main storage"""
        self.ensure_one()
        
        if self.move_type != 'in_invoice':
            raise UserError(_("Stock moves can only be created for supplier invoices."))
            
        # Get NOVEM IT warehouse and its stock location
        warehouse = self.env['stock.warehouse'].search([
            ('name', '=', 'NOVEM IT, s.r.o.'),
            ('company_id', '=', self.company_id.id)
        ], limit=1)
        
        if not warehouse:
            raise UserError(_("Warehouse 'NOVEM IT, s.r.o.' not found."))
            
        storage_location = warehouse.lot_stock_id
        if not storage_location:
            raise UserError(_("Stock location not found in warehouse 'NOVEM IT, s.r.o.'."))
            
        supplier_location = self.env.ref('stock.stock_location_suppliers')
        picking_type = warehouse.in_type_id
        
        if not picking_type:
            raise UserError(_("No incoming operation type found for warehouse 'NOVEM IT, s.r.o.'."))
        
        moves_to_create = []
        for line in self.invoice_line_ids:
            # Skip lines without products
            if not line.product_id and not line.name:
                continue
                
            # If product doesn't exist, create it
            product = line.product_id
            if not product:
                # Create product from the invoice line
                product = self.env['product.product'].create({
                    'name': line.name,
                    'type': 'product',  # Storable product
                    'detailed_type': 'product',
                    'categ_id': self.env.ref('product.product_category_all').id,
                    'standard_price': line.price_unit,
                    'list_price': line.price_unit,
                    'uom_id': line.product_uom_id.id or self.env.ref('uom.product_uom_unit').id,
                    'uom_po_id': line.product_uom_id.id or self.env.ref('uom.product_uom_unit').id,
                    'invoice_policy': 'order',
                    'purchase_method': 'purchase',
                })
                # Update the invoice line with the new product
                line.product_id = product.id
            
            # Create stock move with direct link to invoice line
            move_vals = {
                'name': f"{self.name}: {line.name or product.name}",
                'product_id': product.id,
                'product_uom': line.product_uom_id.id or product.uom_id.id,
                'product_uom_qty': line.quantity,
                'price_unit': line.price_unit,
                'location_id': supplier_location.id,
                'location_dest_id': storage_location.id,
                'picking_type_id': picking_type.id,
                'company_id': self.company_id.id,
                'invoice_line_id': line.id,  # Link to the specific invoice line
                'origin': self.name,
                'state': 'draft',
                'purchase_line_id': False,  # Ensure no conflict with purchase orders
            }
            moves_to_create.append(move_vals)
            
        if not moves_to_create:
            raise UserError(_("No valid products found to create stock moves."))
            
        # Create stock moves
        stock_moves = self.env['stock.move'].create(moves_to_create)
        
        # Create picking for the moves
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': supplier_location.id,
            'location_dest_id': storage_location.id,
            'origin': self.name,
            'move_ids': [(6, 0, stock_moves.ids)],
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'move_type': 'direct',  # This is for the picking's move type, not related to invoice move_type
        })
        
        # Confirm all moves
        stock_moves.write({'picking_id': picking.id})
        picking.action_confirm()
        picking.action_assign()
        
        # Create move lines with correct quantities and validate
        move_lines = []
        for move in stock_moves:
            move_lines.append(self.env['stock.move.line'].create({
                'move_id': move.id,
                'product_id': move.product_id.id,
                'product_uom_id': move.product_uom.id,
                'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id,
                'picking_id': picking.id,
                'qty_done': move.product_uom_qty,  # Set the done quantity
            }))
        
        # Mark the picking as done
        picking.with_context(skip_backorder=True)._action_done()
        
        # Add traceability messages
        picking.message_post(
            body=_("Created from supplier invoice %s") % self.name,
            subtype_id=self.env.ref('mail.mt_note').id)
        self.message_post(
            body=_("Created stock receipt %s") % picking.name,
            subtype_id=self.env.ref('mail.mt_note').id)
            
        return True

    @api.depends('invoice_line_ids.stock_move_ids')
    def _compute_has_stock_moves(self):
        for record in self:
            record.has_stock_moves = bool(record.invoice_line_ids.mapped('stock_move_ids'))

    @api.depends('invoice_line_ids.stock_move_ids.picking_id', 'move_type')
    def _compute_picking_ids(self):
        for record in self:
            if record.move_type != 'in_invoice':
                record.picking_ids = False
                record.picking_count = 0
                continue

            all_moves = self.env['stock.move'].search([
                ('invoice_line_id', 'in', record.invoice_line_ids.ids)
            ])
            pickings = all_moves.mapped('picking_id')
            record.picking_ids = pickings
            record.picking_count = len(pickings)

    @api.depends('picking_ids', 'picking_ids.state', 'invoice_line_ids.product_id.type')
    def _compute_stock_state(self):
        for record in self:
            if not any(line.product_id.type == 'product' for line in record.invoice_line_ids):
                record.stock_state = 'no_stock'
                continue

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
            'name': _('Receipts'),
            'view_mode': 'list,form',
            'res_model': 'stock.picking',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.picking_ids.ids)],
            'context': {
                'create': False,
                'default_picking_type_code': 'incoming',
                'search_default_draft': 1,
                'search_default_assigned': 1,
                'search_default_waiting': 1,
            },
        }

    def action_view_stock_moves(self):
        """Show related stock moves"""
        self.ensure_one()
        return {
            'name': _('Stock Moves'),
            'view_mode': 'list,form',
            'res_model': 'stock.move',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.stock_move_ids.ids)],
            'context': {'create': False},
        }

    def action_create_new_product(self):
        """Override standard product creation from invoice line to set defaults"""
        ctx = dict(self.env.context)
        if self.move_type == 'in_invoice':
            # Set defaults for products created from supplier invoice
            ctx.update({
                'default_property_account_income_id': 207,  # "601000 Tržby za vlastné výrobky"
                'is_storable': True,  # Always make products storable

            })
        return {
            'name': _('Create Product'),
            'res_model': 'product.product',
            'view_mode': 'form',
            'view_id': False,
            'target': 'new',
            'type': 'ir.actions.act_window',
            'context': ctx,
        }


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    contract_line_id = fields.Many2one(
        'contract.line',
        string='Contract Line',
        readonly=True,
        index=True,
        help='Contract line that generated this invoice line',
    )
