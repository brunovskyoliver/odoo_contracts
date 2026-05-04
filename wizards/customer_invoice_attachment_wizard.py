# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import io
from datetime import datetime
from logging import getLogger

from odoo import _, api, fields, models
from odoo.exceptions import UserError

try:
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
except ImportError:
    colors = None
    pdfmetrics = None
    TTFont = None
    canvas = None

_logger = getLogger(__name__)

try:
    from PyPDF2 import PdfReader, PdfWriter
except ImportError:
    PdfReader = None
    PdfWriter = None
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
    include_page_banner = fields.Boolean(
        string="Pridať označenie faktúry na stránky",
        default=True,
        help="Pridá na každú stránku krátky pásik s číslom faktúry a partnerom.",
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

    def _get_move_pdf_label(self, move):
        """Return a stable human-readable label for PDF bookmarks/banners."""
        move_name = move.name or move.ref or str(move.id)
        partner_name = move.partner_id.name or "N/A"
        return f"{move_name} | {partner_name}"

    def _get_move_pdf_banner_label(self, move):
        """Return the visible page banner label."""
        move_name = move.name or move.ref or str(move.id)
        partner_name = move.partner_id.name or ""
        if partner_name:
            return f"{move_name} | {partner_name}"
        return move_name

    def _get_move_pdf_sort_key(self, move):
        """Return a sort key that never mixes booleans with strings."""
        move_date = (
            move.taxable_supply_date
            or move.invoice_date
            or move.date
            or fields.Date.today()
        )
        move_name = move.name or move.ref or ""
        partner_name = move.partner_id.name or ""
        return (move_date, str(move_name), str(partner_name), move.id)

    def _get_attachment_pdf_data(self, attachment):
        """Return decoded PDF bytes, preferring Odoo's raw attachment payload."""
        if "raw" in attachment._fields and attachment.raw:
            return attachment.raw
        if attachment.datas:
            return base64.b64decode(attachment.datas)
        return b""

    def _add_pdf_outline_item(self, writer, title, page_number):
        """Add a lightweight invoice bookmark when supported by PyPDF2."""
        try:
            writer.add_outline_item(title, page_number)
        except AttributeError:
            try:
                writer.add_bookmark(title, page_number)
            except Exception as err:
                _logger.debug("Could not add PDF bookmark %s: %s", title, err)
        except Exception as err:
            _logger.debug("Could not add PDF bookmark %s: %s", title, err)

    def _get_pdf_page_size(self, page):
        page_box = getattr(page, "mediabox", None) or page.mediaBox
        width = page_box.width if hasattr(page_box, "width") else page_box.getWidth()
        height = page_box.height if hasattr(page_box, "height") else page_box.getHeight()
        return float(abs(width)), float(abs(height))

    def _fit_banner_text(self, text, max_width, font_name, font_size):
        """Trim banner text by rendered width, preserving the invoice number first."""
        if not text or not pdfmetrics:
            return text
        if pdfmetrics.stringWidth(text, font_name, font_size) <= max_width:
            return text

        suffix = "..."
        available_width = max_width - pdfmetrics.stringWidth(suffix, font_name, font_size)
        if available_width <= 0:
            return suffix

        fitted = ""
        for character in text:
            next_value = fitted + character
            if pdfmetrics.stringWidth(next_value, font_name, font_size) > available_width:
                break
            fitted = next_value
        return f"{fitted.rstrip()}{suffix}"

    def _get_banner_font_name(self):
        """Use a Unicode font so Slovak invoice numbers render correctly."""
        font_name = "DejaVuSans-Bold"
        if not pdfmetrics or not TTFont:
            return "Helvetica-Bold"
        if font_name in pdfmetrics.getRegisteredFontNames():
            return font_name
        try:
            pdfmetrics.registerFont(
                TTFont(font_name, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
            )
            return font_name
        except Exception as err:
            _logger.warning("Could not register PDF banner font %s: %s", font_name, err)
            return "Helvetica-Bold"

    def _build_banner_overlay(self, width, height, text):
        """Create one-page PDF overlay with a bounded horizontal invoice banner."""
        font_name = self._get_banner_font_name()
        font_size = 8
        banner_height = 18
        horizontal_padding = 8
        max_text_width = max(width - (2 * horizontal_padding), 1)
        fitted_text = self._fit_banner_text(text, max_text_width, font_name, font_size)

        overlay_stream = io.BytesIO()
        pdf_canvas = canvas.Canvas(overlay_stream, pagesize=(width, height))
        pdf_canvas.setFillColor(colors.Color(113 / 255, 75 / 255, 103 / 255, 0.86))
        pdf_canvas.rect(0, height - banner_height, width, banner_height, fill=1, stroke=0)
        pdf_canvas.setFont(font_name, font_size)
        pdf_canvas.setFillColor(colors.white)
        pdf_canvas.drawString(horizontal_padding, height - 12, fitted_text)
        pdf_canvas.save()
        overlay_stream.seek(0)
        return overlay_stream

    def _merge_pdf_page_overlay(self, page, overlay_page):
        if hasattr(page, "merge_page"):
            page.merge_page(overlay_page)
        else:
            page.mergePage(overlay_page)

    def _add_page_banner_to_pdf(self, pdf_stream, text):
        if not canvas or not colors:
            raise UserError(_("ReportLab library is not installed. Please install it to add PDF banners."))

        reader = PdfReader(pdf_stream, strict=False)
        if getattr(reader, "is_encrypted", False):
            reader.decrypt("")

        writer = PdfWriter()
        for page in reader.pages:
            if "/Annots" in page:
                del page["/Annots"]
            width, height = self._get_pdf_page_size(page)
            overlay_stream = self._build_banner_overlay(width, height, text)
            overlay_page = PdfReader(overlay_stream, strict=False).pages[0]
            self._merge_pdf_page_overlay(page, overlay_page)
            writer.add_page(page)

        output_stream = io.BytesIO()
        writer.write(output_stream)
        output_stream.seek(0)
        return output_stream

    def _prepare_pdf_reader(self, pdf_stream, move, banner_text):
        """Return a PdfReader for the invoice PDF, optionally with a page banner."""
        pdf_stream.seek(0)
        if self.include_page_banner:
            try:
                pdf_stream = self._add_page_banner_to_pdf(pdf_stream, banner_text)
            except Exception as banner_err:
                _logger.warning(
                    "Could not add banner to invoice %s: %s",
                    move.name or move.id,
                    banner_err,
                )
                pdf_stream.seek(0)

        reader = PdfReader(pdf_stream, strict=False)
        if getattr(reader, "is_encrypted", False):
            reader.decrypt("")
        return reader

    def _merge_pdfs(self, moves_with_attachments):
        """Merge multiple invoice PDFs into one with lightweight bookmarks."""
        if not PdfWriter:
            raise UserError(_("PyPDF2 library is not installed. Please install it to use this feature."))
        
        writer = PdfWriter()
        successful_merges = 0
        errors = []
        page_count = 0
        invoice_streams = self._get_invoice_pdf_streams(moves_with_attachments, errors)
        
        for move in moves_with_attachments:
            try:
                pdf_stream = invoice_streams.get(move.id)
                if not pdf_stream:
                    errors.append(f"No PDF stream found for invoice {move.name or move.id}")
                    continue

                outline_text = self._get_move_pdf_label(move)
                banner_text = self._get_move_pdf_banner_label(move)
                reader = self._prepare_pdf_reader(pdf_stream, move, banner_text)
                source_page_count = len(reader.pages)

                if source_page_count == 0:
                    errors.append(f"Empty PDF: {move.name or move.id}")
                    continue

                first_output_page = page_count
                for page in reader.pages:
                    writer.add_page(page)
                    page_count += 1

                self._add_pdf_outline_item(writer, outline_text, first_output_page)
                successful_merges += 1
                    
            except Exception as e:
                errors.append(f"Error with {move.name or move.id}: {str(e)}")
                _logger.warning(
                    "Failed to merge PDF for invoice %s: %s", move.name or move.id, e
                )
        
        if successful_merges == 0:
            raise UserError(_("No valid PDFs could be merged. Errors:\n%s") % "\n".join(errors))
        
        # Write merged PDF to bytes
        output_stream = io.BytesIO()
        writer.write(output_stream)
        merged_data = output_stream.getvalue()
        
        return merged_data, successful_merges, errors, page_count

    def _get_invoice_report_action(self):
        for xmlid in (
            "account.account_invoices",
            "account.report_invoice",
            "account.report_invoice_with_payments",
        ):
            report = self.env.ref(xmlid, raise_if_not_found=False)
            if report:
                return report, xmlid
        raise UserError(_("Invoice PDF report action not found."))

    def _get_invoice_attachment_pdf_stream(self, move):
        """Return the stored invoice PDF stream, when available."""
        attachment = move.message_main_attachment_id
        if attachment and attachment.mimetype == "application/pdf":
            pdf_data = self._get_attachment_pdf_data(attachment)
            if pdf_data:
                return io.BytesIO(pdf_data)
        return False

    def _render_invoice_pdf_streams_batch(self, report, report_ref, moves):
        """Render missing invoice PDFs in one wkhtmltopdf call where possible."""
        if not moves:
            return {}

        collected_streams, report_type = report._pre_render_qweb_pdf(
            report_ref,
            res_ids=moves.ids,
        )
        if report_type != "pdf":
            raise UserError(_("Invoice report did not render as PDF."))

        # If Odoo cannot split a multi-invoice batch, fall back to individual
        # rendering for this chunk so banners/bookmarks still match invoices.
        if False in collected_streams and len(moves) > 1:
            streams = {}
            for move in moves:
                streams.update(self._render_invoice_pdf_streams_batch(report, report_ref, move))
            return streams
        if False in collected_streams and len(moves) == 1:
            false_stream = collected_streams[False].get("stream")
            if false_stream:
                false_stream.seek(0)
                return {moves.id: false_stream}

        streams = {}
        for move in moves:
            stream_data = collected_streams.get(move.id)
            if stream_data and stream_data.get("stream"):
                stream_data["stream"].seek(0)
                streams[move.id] = stream_data["stream"]
        return streams

    def _get_invoice_pdf_streams(self, moves, errors):
        """Return invoice PDF streams, batch-rendering only missing PDFs."""
        streams = {}
        moves_to_render = self.env["account.move"]

        for move in moves:
            attachment_stream = self._get_invoice_attachment_pdf_stream(move)
            if attachment_stream:
                streams[move.id] = attachment_stream
            else:
                moves_to_render |= move

        if not moves_to_render:
            return streams

        report, report_ref = self._get_invoice_report_action()
        batch_size = max(self.batch_size or 20, 1)
        _logger.info(
            "Rendering %s missing customer invoice PDFs in batches of %s",
            len(moves_to_render),
            batch_size,
        )

        for start in range(0, len(moves_to_render), batch_size):
            batch = moves_to_render[start:start + batch_size]
            try:
                streams.update(self._render_invoice_pdf_streams_batch(report, report_ref, batch))
            except Exception as err:
                for move in batch:
                    errors.append(f"Could not generate PDF for invoice {move.name or move.id}: {err}")
                _logger.warning(
                    "Failed to batch-render customer invoice PDFs %s: %s",
                    batch.ids,
                    err,
                )

        return streams

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
            moves_with_attachments = self.move_ids.sorted(key=self._get_move_pdf_sort_key)
            
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
            
            _logger.info(
                "Successfully created merged PDF: %s (%s invoices, %s pages)",
                filename,
                successful_count,
                page_count,
            )
            
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
