# Copyright 2016 Tecnativa - Carlos Dauden
# Copyright 2018 ACSONE SA/NV.
# Copyright 2020 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models, api, _
from odoo.exceptions import UserError
from datetime import timedelta


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

    amount_untaxed_rounded = fields.Monetary(
        string='Untaxed Amount (Rounded)',
        compute='_compute_rounded_amounts',
        store=True,
    )
    amount_tax_rounded = fields.Monetary(
        string='Tax (Rounded)',
        compute='_compute_rounded_amounts',
        store=True,
    )
    amount_total_rounded = fields.Monetary(
        string='Total (Rounded)',
        compute='_compute_rounded_amounts',
        store=True,
    )

    @api.model
    def _get_default_invoice_date_due(self):
        return fields.Date.context_today(self) + timedelta(days=14)

    invoice_date_due = fields.Date(
        default=_get_default_invoice_date_due,
    )

    def action_create_stock_moves(self):
        """Open wizard to select storage location"""
        self.ensure_one()
        
        if self.move_type != 'in_invoice':
            raise UserError(_("Stock moves can only be created for supplier invoices."))
            
        return {
            'name': _('Select Storage Location'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.location.select.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_invoice_id': self.id},
        }
        
    def create_stock_moves(self):
        """Create stock moves for invoice lines"""
        self.ensure_one()
        default_warehouse = self.env['stock.warehouse'].browse(self._context.get('selected_warehouse_id'))
        if not default_warehouse:
            raise UserError(_("Please select a default storage location"))
            
        supplier_location = self.env.ref('stock.stock_location_suppliers')
        pickings_by_warehouse = {}
        
        # Filter lines: must have product AND account number must be 501000
        for line in self.invoice_line_ids.filtered(lambda l: l.product_id and l.account_id.code == '501000'):
            # Determine warehouse for this line
            warehouse = line.where_to_move or default_warehouse
            
            # Get or create picking for this warehouse
            if warehouse not in pickings_by_warehouse:
                picking_type = warehouse.in_type_id
                picking = self.env['stock.picking'].create({
                    'picking_type_id': picking_type.id,
                    'location_id': supplier_location.id,
                    'location_dest_id': warehouse.lot_stock_id.id,
                    'origin': self.name,
                    'partner_id': self.partner_id.id,
                    'company_id': self.company_id.id,
                })
                pickings_by_warehouse[warehouse] = picking
            else:
                picking = pickings_by_warehouse[warehouse]
            
            # Create stock move
            self.env['stock.move'].create({
                'name': line.name or line.product_id.name,
                'product_id': line.product_id.id,
                'product_uom': line.product_uom_id.id or line.product_id.uom_id.id,
                'product_uom_qty': line.quantity,
                'price_unit': line.price_unit,
                'picking_id': picking.id,
                'location_id': supplier_location.id,
                'location_dest_id': warehouse.lot_stock_id.id,
                'invoice_line_id': line.id,
            })
            
        # Process all pickings
        for picking in pickings_by_warehouse.values():
            picking.action_confirm()
            picking.action_assign()
            
            # Create move lines and set quantities
            for move in picking.move_ids:
                self.env['stock.move.line'].create({
                    'move_id': move.id,
                    'product_id': move.product_id.id,
                    'product_uom_id': move.product_uom.id,
                    'location_id': move.location_id.id,
                    'location_dest_id': move.location_dest_id.id,
                    'picking_id': picking.id,
                    'qty_done': move.product_uom_qty,
                })
                
            picking._action_done()
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

    @api.depends('line_ids.amount_currency', 'line_ids.tax_base_amount', 'line_ids.tax_line_id', 'partner_id', 'currency_id', 'amount_total', 'amount_untaxed')
    def _compute_tax_totals(self):
        """Override to round amounts to 2 decimals after tax calculation"""
        # First call the original method to calculate taxes
        super()._compute_tax_totals()
        
        # Then round all the amounts to 2 decimals
        for move in self:
            if move.tax_totals:
                # Round amounts in tax_totals
                if 'amount_untaxed' in move.tax_totals:
                    move.tax_totals['amount_untaxed'] = round(move.tax_totals['amount_untaxed'], 2)
                if 'amount_total' in move.tax_totals:
                    move.tax_totals['amount_total'] = round(move.tax_totals['amount_total'], 2)
                
                # Round tax group amounts
                if 'groups_by_subtotal' in move.tax_totals:
                    for groups in move.tax_totals['groups_by_subtotal'].values():
                        for group in groups:
                            if 'tax_group_amount' in group:
                                group['tax_group_amount'] = round(group['tax_group_amount'], 2)
                            if 'tax_group_base_amount' in group:
                                group['tax_group_base_amount'] = round(group['tax_group_base_amount'], 2)
                
                # Round the amounts on the move record itself
                move.amount_untaxed = round(move.amount_untaxed, 2)
                move.amount_tax = round(move.amount_tax, 2)
                move.amount_total = round(move.amount_total, 2)
                move.amount_residual = round(move.amount_residual, 2)
                
                # Round the signed amounts as well
                move.amount_untaxed_signed = round(move.amount_untaxed_signed, 2)
                move.amount_tax_signed = round(move.amount_tax_signed, 2)
                move.amount_total_signed = round(move.amount_total_signed, 2)
                move.amount_residual_signed = round(move.amount_residual_signed, 2)

    def _compute_payments_widget_to_reconcile_info(self):
        """Override to round the amounts shown in the payments widget"""
        super()._compute_payments_widget_to_reconcile_info()
        for move in self:
            if move.invoice_outstanding_credits_debits_widget:
                for line in move.invoice_outstanding_credits_debits_widget['content']:
                    line['amount'] = round(line['amount'], 2)
                    if 'amount_currency' in line:
                        line['amount_currency'] = round(line['amount_currency'], 2)

    def _get_reconciled_info_JSON_values(self):
        """Override to round the amounts in reconciliation info"""
        vals = super()._get_reconciled_info_JSON_values()
        for val in vals:
            val['amount'] = round(val['amount'], 2)
            if 'amount_currency' in val:
                val['amount_currency'] = round(val['amount_currency'], 2)
        return vals

    def _compute_amount(self):
        """Override to ensure amounts are rounded in amount computation"""
        super()._compute_amount()
        for move in self:
            if move.move_type not in ['entry', 'out_receipt', 'in_receipt']:
                move.amount_untaxed = round(move.amount_untaxed, 2)
                move.amount_tax = round(move.amount_tax, 2)
                move.amount_total = round(move.amount_total, 2)
                move.amount_residual = round(move.amount_residual, 2)
                move.amount_untaxed_signed = round(move.amount_untaxed_signed, 2)
                move.amount_tax_signed = round(move.amount_tax_signed, 2)
                move.amount_total_signed = round(move.amount_total_signed, 2)
                move.amount_residual_signed = round(move.amount_residual_signed, 2)

    @api.depends('amount_total')
    def _compute_amount_total_words(self):
        """Override to use rounded amount in words computation"""
        for move in self:
            move.amount_total_words = move.currency_id.amount_to_text(round(move.amount_total, 2))

    def _recompute_dynamic_lines(self, recompute_all_taxes=False, recompute_tax_base_amount=False):
        res = super()._recompute_dynamic_lines(
            recompute_all_taxes=recompute_all_taxes,
            recompute_tax_base_amount=recompute_tax_base_amount,
        )

        currency = self.currency_id or self.company_id.currency_id
        precision = currency.decimal_places or 2

        for move in self:
            if not move.line_ids:
                continue

            debit_total = sum(line.debit for line in move.line_ids)
            credit_total = sum(line.credit for line in move.line_ids)

            diff = round(debit_total - credit_total, precision + 2)

            if abs(diff) >= 10**(-precision):
                if abs(diff) < 0.01:
                    # Fix the last receivable/payable line
                    receivable_lines = move.line_ids.filtered(lambda l: l.account_id.internal_type in ('receivable', 'payable') and not l.display_type)
                    if receivable_lines:
                        last_line = receivable_lines.sorted(key=lambda l: l.date_maturity or move.invoice_date_due or move.invoice_date)[-1]
                        if diff > 0:
                            last_line.credit += diff
                        else:
                            last_line.debit += -diff
                    else:
                        raise UserError(_("Could not find a receivable/payable line to adjust."))
                else:
                    raise UserError(_(
                        "Move is unbalanced by %.4f EUR.\nDebit: %.4f\nCredit: %.4f"
                    ) % (diff, debit_total, credit_total))

        return res



    @api.depends('amount_untaxed', 'amount_tax', 'amount_total')
    def _compute_rounded_amounts(self):
        """Compute rounded amounts for display"""
        for move in self:
            move.amount_untaxed_rounded = round(move.amount_untaxed, 2)
            move.amount_tax_rounded = round(move.amount_tax, 2)
            move.amount_total_rounded = round(move.amount_total, 2)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    contract_line_id = fields.Many2one(
        'contract.line',
        string='Contract Line',
        readonly=True,
        index=True,
        help='Contract line that generated this invoice line',
    )
    
    where_to_move = fields.Many2one(
        'stock.warehouse',
        string='Sklad',
        help='Specific warehouse to move this line to. If not set, will use the globally selected warehouse.'
    )
