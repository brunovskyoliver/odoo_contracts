# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from datetime import datetime


class ContractInventoryLocation(models.Model):
    _name = 'contract.inventory.location'
    _description = 'Contract Inventory Location'
    _order = 'name'

    name = fields.Char(string='Názov miesta', required=True)
    active = fields.Boolean(default=True)
    inventory_id = fields.Many2one(
        'contract.inventory',
        string='Inventory',
        required=True,
        ondelete='cascade'
    )


class ContractInventory(models.Model):
    _name = "contract.inventory"
    _description = "Skladovanie inventára zmluvy"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Názov", required=True, copy=False, tracking=True)
    location_ids = fields.One2many(
        'contract.inventory.location',
        'inventory_id',
        string='Locations'
    )
    code = fields.Char(string="Kód", copy=False, tracking=True)
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Predvolený sklad',
        help='Predvolený sklad pre skladové operácie',
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
        string="Zmluvy",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company", 
        string="Spoločnosť",
        default=lambda self: self.env.company,
    )
    note = fields.Text(string="Poznámky")
    inventory_line_ids = fields.One2many(
        comodel_name="contract.inventory.line",
        inverse_name="inventory_id",
        string="Inventárne riadky",
    )
    total_products = fields.Integer(
        string="Spolu produkty",
        compute="_compute_total_products",
        store=True,
    )
    picking_ids = fields.Many2many(
        'stock.picking',
        string="Súvisiace skladové pohyby",
        compute='_compute_picking_ids',
        store=True,
    )
    picking_count = fields.Integer(
        compute='_compute_picking_ids',
        store=True,
        string="Počet pohybov",
    )
    stock_state = fields.Selection([
        ('pending', 'Čakajúce'),
        ('partial', 'Čiastočne spracované'),
        ('done', 'Úplne spracované'),
        ('cancelled', 'Zrušené'),
    ], string="Stav skladu", compute='_compute_stock_state', store=True)

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
        """Zobraziť súvisiace pohyby"""
        self.ensure_one()
        return {
            'name': _('Skladové operácie'),
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
        """Spracovať skladové pohyby pre všetky čakajúce riadky"""
        self.ensure_one()
        if not self.warehouse_id:
            raise UserError(_("Najprv vyberte sklad"))

        pending_lines = self.inventory_line_ids.filtered(lambda l: l.state == 'draft')
        if not pending_lines:
            raise UserError(_("Žiadne čakajúce riadky na spracovanie"))

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
    _description = "Riadok inventára zmluvy"

    def unlink(self):
        return super().unlink()
    _rec_name = "product_id"
    _inherit = ['mail.thread']

    inventory_id = fields.Many2one(
        comodel_name="contract.inventory",
        string="Inventár",
        required=True,
        ondelete="cascade",
    )
    location_id = fields.Many2one(
        comodel_name="contract.inventory.location",
        string="Miesto",
        domain="[('inventory_id', '=', inventory_id)]"
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Produkt",
        required=True,
        domain="[('qty_available', '>', 0)]",  # Iba produkty s dostupným skladom
    )
    contract_line_id = fields.Many2one(
        comodel_name="contract.line",
        string="Riadok zmluvy",
    )
    quantity = fields.Float(
        string="Množstvo",
        default=1.0,
        required=True,
    )
    uom_id = fields.Many2one(
        related="product_id.uom_id",
        string="Merná jednotka",
    )
    date_added = fields.Date(
        string="Dátum pridania",
        default=fields.Date.context_today,
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Návrh'),
            ('pending', 'Čakajúce'),
            ('assigned', 'Priradené'),
            ('returned', 'Vrátené'),
        ],
        string="Stav",
        default='draft',
        tracking=True,
    )
    stock_move_ids = fields.One2many(
        'stock.move',
        'contract_inventory_line_id',
        string="Skladové pohyby",
        readonly=True,
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Sklad',
        help='Konkrétny sklad, z/do ktorého sa má položka presunúť.',
    )
    contract_id = fields.Many2one(
        related="contract_line_id.contract_id",
        string="Zmluva",
        store=True,
    )
    serial_number = fields.Char(
        string="Sériové číslo",
    )
    note = fields.Text(string="Poznámky")

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.uom_id = self.product_id.uom_id.id

    def process_stock_movement(self):
        """Spracovať skladový pohyb pre tento riadok"""
        self.ensure_one()
        
        # Skip stock movement if context flag is set
        if self.env.context.get('no_stock_movement'):
            return False
            
        quantity = self.env.context.get('quantity', self.quantity)

        if not self.warehouse_id and not self.inventory_id.warehouse_id:
            raise UserError(_("Vyberte sklad pre riadok alebo inventár"))

        warehouse = self.warehouse_id or self.inventory_id.warehouse_id
        
        # Určenie typu operácie
        if self.state == 'returned':
            picking_type = warehouse.in_type_id
            location_src = self.env.ref('stock.stock_location_customers')
            location_dest = warehouse.lot_stock_id
        else:
            picking_type = warehouse.out_type_id
            location_src = warehouse.lot_stock_id
            location_dest = self.env.ref('stock.stock_location_customers')

        # Nájsť alebo vytvoriť príjemku/výdajku
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
                'origin': f'Inventár zmluvy {self.inventory_id.name}',
                'company_id': self.inventory_id.company_id.id,
            })

        # Vytvoriť skladový pohyb
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
            'origin': f'Inventár zmluvy {self.inventory_id.name}',
        }

        move = self.env['stock.move'].create(move_vals)

        picking.action_confirm()

        # Vytvoriť riadok pohybu
        for move in picking.move_ids_without_package:
            self.env['stock.move.line'].create({
                'move_id': move.id,
                'picking_id': picking.id,
                'product_id': move.product_id.id,
                'product_uom_id': move.product_uom.id,
                'qty_done': quantity,
                'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id,
            })

        picking.with_context(skip_backorder=True).button_validate()
        self.write({'state': 'assigned'})
        
        return move

    def _return_to_stock(self, qty):
        """Vytvoriť vrátenie skladového pohybu pre zadané množstvo"""
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.env.ref('stock.picking_type_in').id,
            'location_id': self.env.ref('stock.stock_location_customers').id,
            'location_dest_id': self.env.ref('stock.stock_location_stock').id,
            'partner_id': self.contract_id.partner_id.id,
            'origin': f'Vrátenie inventára zmluvy {self.inventory_id.name}',
            'company_id': self.inventory_id.company_id.id,
        })

        stock_move = self.env['stock.move'].create({
            'name': _('Vrátenie inventára zmluvy: %s') % self.inventory_id.name,
            'product_id': self.product_id.id,
            'product_uom_qty': qty,
            'product_uom': self.uom_id.id,
            'picking_id': picking.id,
            'location_id': self.env.ref('stock.stock_location_customers').id,
            'location_dest_id': self.env.ref('stock.stock_location_stock').id,
            'company_id': self.inventory_id.company_id.id,
            'picking_type_id': self.env.ref('stock.picking_type_in').id,
            'origin': f'Vrátenie inventára zmluvy {self.inventory_id.name}',
        })

        picking.action_confirm()

        for move in picking.move_ids_without_package:
            self.env['stock.move.line'].create({
                'move_id': move.id,
                'picking_id': picking.id,
                'product_id': move.product_id.id,
                'product_uom_id': move.product_uom.id,
                'qty_done': qty,
                'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id,
            })

        picking.with_context(skip_backorder=True).button_validate()
        return stock_move

    @api.model
    def create(self, vals):
        res = super().create(vals)
        if res.state != 'returned':  # Nevytvárať pohyb pre už vrátené položky
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
                
                if new_qty != old_qty and new_state != 'returned':
                    if new_qty > old_qty:
                        self.with_context(quantity=new_qty - old_qty).process_stock_movement()
                    else:
                        self.write({'state': 'returned'})
                        self.with_context(quantity=old_qty - new_qty).process_stock_movement()
                
                if old_state != 'returned' and new_state == 'returned':
                    self.with_context(quantity=old_qty).process_stock_movement()
                elif old_state == 'returned' and new_state != 'returned':
                    self.with_context(quantity=new_qty).process_stock_movement()
        
        return res

    @api.constrains('quantity', 'product_id')
    def _check_quantity(self):
        for line in self:
            # Skip validation for products with "Servisné práce" in name
            if 'Servisné práce' in line.product_id.name:
                continue
                
            available_qty = line.product_id.with_context(location=self.env.ref('stock.stock_location_stock').id).qty_available
            if line.quantity > available_qty:
                raise ValidationError(_(
                    "Nie je možné priradiť väčšie množstvo, než je dostupné na sklade. "
                    "Produkt %s má k dispozícii iba %s jednotiek."
                ) % (line.product_id.name, line.product_id.qty_available))
