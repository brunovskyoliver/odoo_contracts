# Copyright 2016 Tecnativa - Carlos Dauden
# Copyright 2018 ACSONE SA/NV.
# Copyright 2020 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import logging
from datetime import timedelta

from odoo import api, fields, models, modules, _
from odoo.exceptions import UserError
from odoo.tools import email_split, float_compare


_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    x_invoice_sent = fields.Boolean(string="Invoice sent", copy=False)

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

    contract_customer_mail_state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("sent", "Sent"),
            ("failed", "Failed"),
            ("skipped", "Skipped"),
        ],
        string="Customer Mail State",
        default="pending",
        copy=False,
        readonly=True,
    )
    contract_customer_mail_attempt_count = fields.Integer(
        string="Customer Mail Attempts",
        default=0,
        copy=False,
        readonly=True,
    )
    contract_customer_mail_last_attempt = fields.Datetime(
        string="Last Customer Mail Attempt",
        copy=False,
        readonly=True,
    )
    contract_customer_mail_failure_reason = fields.Text(
        string="Customer Mail Failure Reason",
        copy=False,
        readonly=True,
    )
    contract_customer_mail_sent_at = fields.Datetime(
        string="Customer Mail Sent At",
        copy=False,
        readonly=True,
    )
    contract_customer_mail_id = fields.Many2one(
        comodel_name="mail.mail",
        string="Customer Mail",
        copy=False,
        readonly=True,
    )

    def copy_data(self, default=None):
        default = dict(default or {})
        if default.get("reversed_entry_id") or default.get("move_type") == "out_refund":
            default.setdefault("x_invoice_sent", False)
            default.setdefault("contract_customer_mail_state", "pending")
            default.setdefault("contract_customer_mail_attempt_count", 0)
            default.setdefault("contract_customer_mail_last_attempt", False)
            default.setdefault("contract_customer_mail_failure_reason", False)
            default.setdefault("contract_customer_mail_sent_at", False)
            default.setdefault("contract_customer_mail_id", False)
        return super().copy_data(default)

    def _contract_invoice_sender_can_commit(self):
        return not modules.module.current_test

    @api.model
    def _contract_customer_document_mail_server(self):
        IrConfig = self.env["ir.config_parameter"].sudo()
        MailServer = self.env["ir.mail_server"].sudo()
        configured_value = (
            IrConfig.get_param("contract.customer_document_mail_server_id") or ""
        ).strip()
        invoice_template = self.env.ref(
            "account.email_template_edi_invoice", raise_if_not_found=False
        )
        candidates = [
            configured_value,
            str(invoice_template.mail_server_id.id)
            if invoice_template and invoice_template.mail_server_id
            else "",
            "fakturacny_smtp",
            "novem_elbia",
            "1",
        ]

        for candidate in candidates:
            if not candidate:
                continue
            server = (
                MailServer.browse(int(candidate)).exists()
                if candidate.isdigit()
                else MailServer.search([("name", "=", candidate)], limit=1)
            )
            if server and ("active" not in server._fields or server.active):
                return server
        return MailServer

    def _contract_customer_document_template(self):
        self.ensure_one()
        xmlid = (
            "contract.email_template_customer_refund"
            if self.move_type == "out_refund"
            else "account.email_template_edi_invoice"
        )
        template = self.env.ref(xmlid, raise_if_not_found=False)
        if not template:
            raise UserError(_("Customer document mail template %s was not found.") % xmlid)
        return template

    def _contract_customer_document_recipient_emails(self, template):
        self.ensure_one()
        rendered_email_to = template._render_field("email_to", [self.id]).get(self.id)
        return email_split(rendered_email_to or "")

    def _contract_customer_document_mail_values(self):
        self.ensure_one()
        values = {"auto_delete": False}
        mail_server = self._contract_customer_document_mail_server()
        if mail_server:
            values["mail_server_id"] = mail_server.id
        return values

    def _contract_customer_document_pdf_attachment(self):
        self.ensure_one()
        report = self.env.ref("account.account_invoices", raise_if_not_found=False)
        if not report:
            raise UserError(_("Customer invoice PDF report was not found."))

        pdf_content, report_format = (
            self.env["ir.actions.report"]
            .sudo()
            .with_context(report_pdf_no_attachment=True)
            ._render_qweb_pdf(report, [self.id])
        )
        if report_format != "pdf":
            raise UserError(_("Customer document report did not render as PDF."))

        filename = "%s.pdf" % (self.name or self.id)
        filename = filename.replace("/", "_")
        return self.env["ir.attachment"].sudo().create(
            {
                "name": filename,
                "datas": base64.b64encode(pdf_content),
                "mimetype": "application/pdf",
                "res_model": self._name,
                "res_id": self.id,
                "type": "binary",
            }
        )

    def _contract_customer_document_create_mail(self, template):
        self.ensure_one()
        values = template._generate_template(
            [self.id],
            (
                "auto_delete",
                "body_html",
                "email_cc",
                "email_from",
                "email_to",
                "mail_server_id",
                "model",
                "partner_to",
                "reply_to",
                "res_id",
                "scheduled_date",
                "subject",
            ),
        )[self.id]

        partner_ids = values.pop("partner_ids", [])
        if partner_ids:
            values["recipient_ids"] = [(4, partner_id) for partner_id in partner_ids]
        values.update(self._contract_customer_document_mail_values())
        attachment = self._contract_customer_document_pdf_attachment()
        values["attachment_ids"] = [(4, attachment.id)]
        values["body"] = values.get("body_html")
        if "email_from" in values and not values.get("email_from"):
            values.pop("email_from")

        return self.env["mail.mail"].sudo().create(values)

    def _contract_customer_document_fail(self, reason, state="failed", mail=False):
        self.ensure_one()
        self.write(
            {
                "contract_customer_mail_state": state,
                "contract_customer_mail_failure_reason": reason,
                "contract_customer_mail_id": mail.id if mail else False,
                "x_invoice_sent": False,
            }
        )
        self.message_post(body=_("Automatic customer email failed: %s") % reason)

    def _contract_send_customer_document(self):
        self.ensure_one()
        if self.state != "posted" or self.move_type not in ("out_invoice", "out_refund"):
            self._contract_customer_document_fail(
                _("Only posted customer invoices and refunds can be sent."),
                state="skipped",
            )
            return False

        mail = self.env["mail.mail"].sudo()
        try:
            template = self._contract_customer_document_template()
            if not self._contract_customer_document_recipient_emails(template):
                self._contract_customer_document_fail(
                    _("No customer email address was rendered by the mail template."),
                    state="skipped",
                )
                return False

            mail = self._contract_customer_document_create_mail(template)
            if not mail:
                self._contract_customer_document_fail(
                    _("The mail template did not create an outgoing email.")
                )
                return False

            mail.send(raise_exception=False)
            mail.invalidate_recordset(
                ["state", "failure_reason", "failure_type", "attachment_ids"]
            )

            if mail.state == "sent":
                self.write(
                    {
                        "contract_customer_mail_state": "sent",
                        "contract_customer_mail_failure_reason": False,
                        "contract_customer_mail_sent_at": fields.Datetime.now(),
                        "contract_customer_mail_id": mail.id,
                        "x_invoice_sent": True,
                        "is_move_sent": True,
                    }
                )
                return True

            failure_reason = mail.failure_reason or mail.failure_type or _(
                "Outgoing email was not accepted by SMTP."
            )
            self._contract_customer_document_fail(failure_reason, mail=mail)
            return False
        except Exception as err:
            _logger.exception(
                "Failed to send customer document %s (%s)",
                self.name or self.id,
                self.id,
            )
            self._contract_customer_document_fail(str(err), mail=mail)
            return False

    @api.model
    def _contract_customer_document_claim_batch(self, batch_size=20):
        stale_processing_cutoff = fields.Datetime.subtract(
            fields.Datetime.now(), hours=1
        )
        self.env.cr.execute(
            """
            SELECT id
              FROM account_move
             WHERE state = 'posted'
               AND move_type IN ('out_invoice', 'out_refund')
               AND COALESCE(x_invoice_sent, false) = false
               AND COALESCE(is_move_sent, false) = false
               AND (
                    contract_customer_mail_state IS NULL
                    OR contract_customer_mail_state IN ('pending', 'failed')
                    OR (
                        contract_customer_mail_state = 'processing'
                        AND (
                            contract_customer_mail_last_attempt IS NULL
                            OR contract_customer_mail_last_attempt < %s
                        )
                    )
               )
             ORDER BY COALESCE(invoice_date, date), id
             FOR UPDATE SKIP LOCKED
             LIMIT %s
            """,
            [stale_processing_cutoff, max(int(batch_size or 20), 1)],
        )
        moves = self.browse([row[0] for row in self.env.cr.fetchall()]).exists()
        now = fields.Datetime.now()
        for move in moves:
            move.write(
                {
                    "contract_customer_mail_state": "processing",
                    "contract_customer_mail_last_attempt": now,
                    "contract_customer_mail_attempt_count": (
                        move.contract_customer_mail_attempt_count or 0
                    )
                    + 1,
                    "contract_customer_mail_failure_reason": False,
                }
            )
        if moves and self._contract_invoice_sender_can_commit():
            self.env.cr.commit()
        return moves

    @api.model
    def cron_send_customer_documents(self, batch_size=20):
        sent_count = 0
        failed_count = 0
        moves = self._contract_customer_document_claim_batch(batch_size=batch_size)
        for move in moves:
            if move._contract_send_customer_document():
                sent_count += 1
            else:
                failed_count += 1
            if self._contract_invoice_sender_can_commit():
                self.env.cr.commit()

        _logger.info(
            "Automatic customer document sender finished: %s sent, %s failed/skipped",
            sent_count,
            failed_count,
        )
        return {"sent": sent_count, "failed": failed_count}

    def message_update(self, msg_dict, update_vals=None):
        result = super().message_update(msg_dict, update_vals=update_vals)
        for move in self:
            move._create_helpdesk_ticket_from_customer_reply(msg_dict)
        return result

    def _create_helpdesk_ticket_from_customer_reply(self, msg_dict):
        self.ensure_one()
        if self.move_type not in ('out_invoice', 'out_refund'):
            return
        if self.state != 'posted':
            return
        if msg_dict.get('message_type') and msg_dict.get('message_type') != 'email':
            return
        if 'helpdesk.ticket' not in self.env or 'helpdesk.team' not in self.env:
            return

        author_id = msg_dict.get('author_id')
        if not author_id or not self.partner_id:
            return

        author_partner = self.env['res.partner'].browse(author_id).exists()
        if not author_partner:
            return

        invoice_partner = self.partner_id.commercial_partner_id
        if author_partner.commercial_partner_id != invoice_partner:
            return

        team = self.env['helpdesk.stage']._get_customer_care_team()
        if not team:
            return

        invoice_label = self.name or self.ref or str(self.id)
        self.env['helpdesk.ticket'].sudo().create({
            'name': _('Odpoved na fakturu %s') % invoice_label,
            'partner_id': invoice_partner.id,
            'team_id': team.id,
            'description': msg_dict.get('body') or _('Zakaznik odpovedal na fakturu %s.') % invoice_label,
        })

    @api.model
    def _get_default_invoice_date_due(self):
        return fields.Date.context_today(self) + timedelta(days=14)

    invoice_date_due = fields.Date(
        default=_get_default_invoice_date_due,
    )

    prenos_danovej_povinnosti = fields.Boolean(
        string='Prenos daňovej povinnosti',
        default=False,
        help='When enabled, all invoice lines will be without VAT rate (VAT liability transfer)',
    )

    @api.onchange('prenos_danovej_povinnosti')
    def _onchange_prenos_danovej_povinnosti(self):
        """Clear VAT taxes from invoice lines when transfer of tax liability is enabled"""
        if self.prenos_danovej_povinnosti:
            for line in self.invoice_line_ids:
                line.tax_ids = [(5, 0, 0)]  # Clear all taxes

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

    def _get_open_payable_lines(self):
        self.ensure_one()
        return self.line_ids.filtered(
            lambda line: (
                not line.reconciled
                and line.account_id.account_type == 'liability_payable'
            )
        )

    def action_pair_with_vendor_counterpart(self):
        self.ensure_one()

        if self.move_type not in ('in_invoice', 'in_refund'):
            raise UserError(_("Only vendor bills and vendor refunds can be paired this way."))
        if self.state != 'posted':
            raise UserError(_("Only posted documents can be paired."))

        payable_lines = self._get_open_payable_lines()
        if not payable_lines:
            raise UserError(_("This document has no open payable lines to reconcile."))

        opposite_move_type = 'in_refund' if self.move_type == 'in_invoice' else 'in_invoice'
        candidates = self.search([
            ('id', '!=', self.id),
            ('state', '=', 'posted'),
            ('move_type', '=', opposite_move_type),
            ('commercial_partner_id', '=', self.commercial_partner_id.id),
            ('company_id', '=', self.company_id.id),
            ('currency_id', '=', self.currency_id.id),
            ('amount_residual', '>', 0),
        ])

        exact_candidates = candidates.filtered(
            lambda move: float_compare(
                move.amount_residual,
                self.amount_residual,
                precision_rounding=self.currency_id.rounding,
            ) == 0
        )

        if len(exact_candidates) > 1:
            raise UserError(_(
                "Multiple counterpart documents with the same open amount were found: %s. "
                "Please reconcile them manually."
            ) % ", ".join(exact_candidates.mapped('display_name')))

        counterpart = exact_candidates[:1]
        if not counterpart:
            if len(candidates) == 1:
                counterpart = candidates
            else:
                raise UserError(_(
                    "No unique counterpart document was found for this vendor. "
                    "Please reconcile it manually."
                ))

        counterpart_payable_lines = counterpart._get_open_payable_lines()
        common_accounts = payable_lines.account_id & counterpart_payable_lines.account_id
        if not common_accounts:
            raise UserError(_(
                "The documents use different payable accounts, so they cannot be paired automatically."
            ))

        for account in common_accounts:
            lines_to_reconcile = (payable_lines + counterpart_payable_lines).filtered(
                lambda line: line.account_id == account and not line.reconciled
            )
            if len(lines_to_reconcile) > 1:
                lines_to_reconcile.reconcile()

        self.message_post(body=_("Paired with %s.") % counterpart.display_name)
        counterpart.message_post(body=_("Paired with %s.") % self.display_name)
        return True

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
