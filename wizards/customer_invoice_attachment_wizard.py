# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import io
import logging
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.pdf import add_banner
from logging import getLogger

_logger = getLogger(__name__)

try:
    from PyPDF2 import PdfMerger, PdfReader
except ImportError:
    PdfMerger = None
    PdfReader = None
    _logger.warning("PyPDF2 not installed. Customer invoice attachment merge will not work.")


class CustomerInvoiceAttachmentWizard(models.TransientModel):
    """Wizard to merge customer invoice attachments into a single PDF."""

    _name = "customer.invoice.attachment.wizard"
    _description = "Customer Invoice Attachment Merge Wizard"
    _merge_notification_emails = "obrunovsky7@gmail.com,oliver.brunovsky@novem.sk,tomas.juricek@novem.sk"

    # Selection mode: from list selection or by date range
    selection_mode = fields.Selection(
        selection=[
            ("selected", "From Selected Invoices"),
            ("date_range", "By Date Range"),
        ],
        string="Selection Mode",
        default="selected",
    )
    
    # Date range filters - using taxable_supply_date
    date_from = fields.Date(string="Date From")
    date_to = fields.Date(string="Date To")
    partner_ids = fields.Many2many(
        comodel_name="res.partner",
        string="Customers (optional)",
        help="Leave empty to include all customers",
    )
    
    move_ids = fields.Many2many(
        comodel_name="account.move",
        string="Selected Invoices",
    )
    move_count = fields.Integer(
        string="Invoice Count",
        compute="_compute_move_count",
    )
    attachment_count = fields.Integer(
        string="Attachments Found",
        compute="_compute_attachment_info",
    )
    missing_attachment_count = fields.Integer(
        string="Missing Attachments",
        compute="_compute_attachment_info",
    )
    missing_attachment_info = fields.Text(
        string="Missing Attachments Info",
        compute="_compute_attachment_info",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("error", "Error"),
        ],
        default="draft",
    )
    result_attachment_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="Result PDF",
        readonly=True,
    )
    download_url = fields.Char(
        string="Download URL",
        compute="_compute_download_url",
    )
    error_message = fields.Text(string="Error Message", readonly=True)
    batch_size = fields.Integer(
        string="Batch Size",
        default=20,
        help="Number of invoices to process at a time to avoid memory issues.",
    )
    progress = fields.Integer(string="Progress (%)", default=0)

    @api.depends("move_ids")
    def _compute_move_count(self):
        for wizard in self:
            wizard.move_count = len(wizard.move_ids)

    @api.depends("result_attachment_id")
    def _compute_download_url(self):
        for wizard in self:
            if wizard.result_attachment_id:
                wizard.download_url = f"/web/content/{wizard.result_attachment_id.id}?download=true"
            else:
                wizard.download_url = False

    @api.depends("move_ids")
    def _compute_attachment_info(self):
        for wizard in self:
            moves_with_attachment = wizard.move_ids.filtered(
                lambda m: m.message_main_attachment_id
                and m.message_main_attachment_id.mimetype == "application/pdf"
            )
            moves_without = wizard.move_ids - moves_with_attachment
            
            wizard.attachment_count = len(moves_with_attachment)
            wizard.missing_attachment_count = len(moves_without)
            
            if moves_without:
                missing_info = []
                for move in moves_without[:10]:  # Show first 10
                    missing_info.append(f"• {move.name} ({move.partner_id.name or 'No Partner'})")
                if len(moves_without) > 10:
                    missing_info.append(f"... and {len(moves_without) - 10} more")
                wizard.missing_attachment_info = "\n".join(missing_info)
            else:
                wizard.missing_attachment_info = False

    @api.model
    def default_get(self, fields_list):
        """Get default values from context."""
        res = super().default_get(fields_list)
        active_ids = self.env.context.get("active_ids", [])
        active_model = self.env.context.get("active_model")
        
        if active_model == "account.move" and active_ids:
            # Filter only customer invoices
            moves = self.env["account.move"].browse(active_ids).filtered(
                lambda m: m.move_type in ("out_invoice", "out_refund")
            )
            res["move_ids"] = [(6, 0, moves.ids)]
            res["selection_mode"] = "selected"
        else:
            # No selection from list, default to date range mode
            res["selection_mode"] = "date_range"
        
        return res

    def action_load_by_date_range(self):
        """Load customer invoices by taxable supply date range."""
        self.ensure_one()
        
        if not self.date_from or not self.date_to:
            raise UserError(_("Please specify both Date From and Date To."))
        
        # Use taxable_supply_date for filtering, fallback to invoice_date if not set
        # Search for invoices where:
        # - taxable_supply_date is in range, OR
        # - taxable_supply_date is not set AND invoice_date is in range
        domain = [
            ("move_type", "in", ("out_invoice", "out_refund")),
            "|",
            "&",
            ("taxable_supply_date", ">=", self.date_from),
            ("taxable_supply_date", "<=", self.date_to),
            "&",
            ("taxable_supply_date", "=", False),
            "&",
            ("invoice_date", ">=", self.date_from),
            ("invoice_date", "<=", self.date_to),
        ]
        
        if self.partner_ids:
            domain.append(("partner_id", "in", self.partner_ids.ids))
        
        moves = self.env["account.move"].search(domain)
        
        self.write({
            "move_ids": [(6, 0, moves.ids)],
        })
        
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _merge_pdfs(self, moves_with_attachments):
        """Merge multiple PDF attachments into one.
        
        Each PDF gets a banner with the invoice reference for identification.
        """
        if not PdfMerger:
            raise UserError(_("PyPDF2 library is not installed. Please install it to use this feature."))
        
        merger = PdfMerger()
        successful_merges = 0
        errors = []
        
        for move in moves_with_attachments:
            try:
                pdf_stream = self._get_invoice_pdf_stream(move)
                
                # Validate PDF before merging
                reader = PdfReader(pdf_stream)
                if len(reader.pages) > 0:
                    pdf_stream.seek(0)
                    
                    # Add banner with invoice reference to identify pages
                    banner_text = f"{move.name} | {move.partner_id.name or 'N/A'}"
                    try:
                        bannered_stream = add_banner(pdf_stream, text=banner_text, logo=False)
                        bannered_stream.seek(0)
                        pdf_to_merge = bannered_stream
                    except Exception as banner_err:
                        _logger.warning(f"Could not add banner to invoice {move.name}: {banner_err}")
                        pdf_stream.seek(0)
                        pdf_to_merge = pdf_stream
                    
                    # Append all pages for customer invoices
                    merger.append(pdf_to_merge)
                    successful_merges += 1
                else:
                    errors.append(f"Empty PDF: {move.name}")
                    
            except Exception as e:
                errors.append(f"Error with {move.name}: {str(e)}")
                _logger.warning(f"Failed to merge PDF for invoice {move.name}: {e}")
        
        if successful_merges == 0:
            raise UserError(_("No valid PDFs could be merged. Errors:\n%s") % "\n".join(errors))
        
        # Write merged PDF to bytes
        output_stream = io.BytesIO()
        merger.write(output_stream)
        merged_data = output_stream.getvalue()
        merger.close()
        
        # Count pages in merged PDF
        page_count = 0
        try:
            merged_reader = PdfReader(io.BytesIO(merged_data))
            page_count = len(merged_reader.pages)
        except Exception:
            pass
        
        return merged_data, successful_merges, errors, page_count

    def _get_invoice_pdf_stream(self, move):
        """Return invoice PDF stream from attachment or generated report."""
        attachment = move.message_main_attachment_id
        if attachment and attachment.mimetype == "application/pdf" and attachment.datas:
            return io.BytesIO(base64.b64decode(attachment.datas))

        report = False
        report_ref = False
        for xmlid in (
            "account.account_invoices",
            "account.report_invoice",
            "account.report_invoice_with_payments",
        ):
            report = self.env.ref(xmlid, raise_if_not_found=False)
            if report:
                report_ref = xmlid
                break

        if not report:
            raise UserError(_("Invoice PDF report action not found."))

        pdf_data, _format = report._render_qweb_pdf(report_ref, res_ids=move.ids)
        if not pdf_data:
            raise UserError(_("Could not generate PDF for invoice %s.") % (move.name or move.id))
        return io.BytesIO(pdf_data)

    def _process_batch(self, moves, batch_num, total_batches):
        """Process a batch of moves and return their PDF attachments."""
        attachments = self.env["ir.attachment"]
        
        for move in moves:
            if move.message_main_attachment_id:
                att = move.message_main_attachment_id
                if att.mimetype == "application/pdf":
                    attachments |= att
        
        _logger.info(f"Batch {batch_num}/{total_batches}: Found {len(attachments)} PDF attachments from {len(moves)} moves")
        return attachments

    def _send_merge_notification(self, subject, body_html, attachment=None):
        """Send notification email for merge results."""
        self.ensure_one()
        try:
            mail_values = {
                "email_from": "novem@novem.sk",
                "email_to": self._merge_notification_emails,
                "subject": subject,
                "body_html": body_html,
            }
            if attachment:
                mail_values["attachment_ids"] = [(4, attachment.id)]
            self.env["mail.mail"].sudo().create(mail_values).send()
        except Exception as mail_err:
            _logger.warning(f"Failed to send merge notification email: {mail_err}")

    def action_generate_merged_pdf(self):
        """Generate a single merged PDF from all selected customer invoices."""
        self.ensure_one()
        
        if not self.move_ids:
            raise UserError(_("No invoices selected."))
        
        self.write({"state": "processing", "progress": 0})
        
        try:
            # Process all selected moves, sorted for consistent ordering.
            moves_with_attachments = self.move_ids.sorted(
                key=lambda m: (m.taxable_supply_date or m.invoice_date or m.date or fields.Date.today(), m.name)
            )
            
            _logger.info(f"Starting PDF merge for {len(moves_with_attachments)} selected customer invoices")
            
            # Merge all PDFs
            merged_pdf_data, successful_count, errors, page_count = self._merge_pdfs(moves_with_attachments)
            
            # Create the result attachment
            filename = f"customer_invoices_merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            attachment = self.env["ir.attachment"].create({
                "name": filename,
                "datas": base64.b64encode(merged_pdf_data),
                "mimetype": "application/pdf",
                "res_model": "customer.invoice.merged.pdf",
                "res_id": 0,
            })
            
            # Create merged PDF record for later access
            merged_pdf_name = f"Merged Customer Invoices {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            merged_pdf_record = self.env["customer.invoice.merged.pdf"].create({
                "name": merged_pdf_name,
                "attachment_id": attachment.id,
                "invoice_count": successful_count,
                "page_count": page_count,
                "move_ids": [(6, 0, moves_with_attachments.ids)],
                "notes": "\n".join(errors[:10]) if errors else False,
            })
            
            # Update attachment to link to the record
            attachment.write({
                "res_id": merged_pdf_record.id,
            })
            
            error_msg = ""
            if errors:
                error_msg = _("Merged %d/%d PDFs. Some errors occurred:\n%s") % (
                    successful_count, len(moves_with_attachments), "\n".join(errors[:10])
                )
            
            self.write({
                "state": "done",
                "result_attachment_id": attachment.id,
                "error_message": error_msg or False,
                "progress": 100,
            })

            status_label = "DOKONČENÉ S CHYBAMI" if errors else "DOKONČENÉ"
            email_body = (
                f"<p>Zlúčenie PDF zákazníckych faktúr: <strong>{status_label}</strong>.</p>"
                f"<p>Celkom vybraných faktúr: {len(moves_with_attachments)}<br/>"
                f"Úspešne zlúčených: {successful_count}<br/>"
                f"Celkový počet strán: {page_count}</p>"
            )
            if errors:
                errors_preview = "\n".join(errors[:20])
                email_body += f"<p><strong>Chyby (prvých 20):</strong></p><pre>{errors_preview}</pre>"
            self._send_merge_notification(
                subject=f"Zlúčenie zákazníckych faktúr {status_label}",
                body_html=email_body,
                attachment=attachment,
            )
            
            _logger.info(f"Successfully created merged PDF: {filename} ({successful_count} pages merged)")
            
            # Stay on the wizard so user can download manually
            return {
                "type": "ir.actions.act_window",
                "res_model": self._name,
                "res_id": self.id,
                "view_mode": "form",
                "target": "new",
            }
            
        except Exception as e:
            _logger.exception("Error generating merged PDF")
            self.write({
                "state": "error",
                "error_message": str(e),
            })
            self._send_merge_notification(
                subject="Zlúčenie zákazníckych faktúr ZLYHALO",
                body_html=(
                    "<p>Zlúčenie PDF zákazníckych faktúr <strong>ZLYHALO</strong>.</p>"
                    f"<p>Chyba: {str(e)}</p>"
                    f"<p>Vybraných faktúr: {len(self.move_ids)}</p>"
                ),
            )
            raise UserError(_("Error generating PDF: %s") % str(e))

    def action_download_result(self):
        """Download the generated PDF."""
        self.ensure_one()
        if not self.result_attachment_id:
            raise UserError(_("No PDF has been generated yet."))
        
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.result_attachment_id.id}?download=true",
            "target": "new",
        }

    def action_retry(self):
        """Retry generation after an error."""
        self.write({
            "state": "draft",
            "error_message": False,
            "result_attachment_id": False,
            "progress": 0,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class CustomerInvoiceMergedPdf(models.Model):
    """Store merged customer invoice PDFs for later reference."""

    _name = "customer.invoice.merged.pdf"
    _description = "Merged Customer Invoice PDF"
    _order = "create_date desc"

    name = fields.Char(string="Name", required=True)
    attachment_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="PDF Attachment",
        required=True,
        ondelete="cascade",
    )
    invoice_count = fields.Integer(string="Invoice Count")
    page_count = fields.Integer(string="Page Count")
    move_ids = fields.Many2many(
        comodel_name="account.move",
        string="Included Invoices",
    )
    notes = fields.Text(string="Notes")

    def action_download(self):
        """Download the merged PDF."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.attachment_id.id}?download=true",
            "target": "new",
        }
