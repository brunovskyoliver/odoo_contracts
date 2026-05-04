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
    _logger.warning("PyPDF2 not installed. Vendor bill attachment merge will not work.")


class VendorBillAttachmentWizard(models.TransientModel):
    """Wizard to merge original vendor bill attachments into a single PDF."""

    _name = "vendor.bill.attachment.wizard"
    _description = "Vendor Bill Attachment Merge Wizard"

    # Selection mode: from list selection or by date range
    selection_mode = fields.Selection(
        selection=[
            ("selected", "From Selected Invoices"),
            ("date_range", "By Date Range"),
        ],
        string="Selection Mode",
        default="selected",
    )
    
    # Date range filters
    date_from = fields.Date(string="Date From")
    date_to = fields.Date(string="Date To")
    partner_ids = fields.Many2many(
        comodel_name="res.partner",
        string="Vendors (optional)",
        help="Leave empty to include all vendors",
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
            # Filter only vendor bills with attachments capability
            moves = self.env["account.move"].browse(active_ids).filtered(
                lambda m: m.move_type in ("in_invoice", "in_refund", "in_receipt")
            )
            res["move_ids"] = [(6, 0, moves.ids)]
            res["selection_mode"] = "selected"
        else:
            # No selection from list, default to date range mode
            res["selection_mode"] = "date_range"
        
        return res

    def action_load_by_date_range(self):
        """Load vendor bills by date range (using accounting date)."""
        self.ensure_one()
        
        if not self.date_from or not self.date_to:
            raise UserError(_("Please specify both Date From and Date To."))
        
        domain = [
            ("move_type", "in", ("in_invoice", "in_refund", "in_receipt")),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
            ("message_main_attachment_id", "!=", False),
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

    def _is_telecom_vendor(self, partner_name):
        """Check if the vendor is O2 or Telekom (only first page needed)."""
        if not partner_name:
            return False
        name_lower = partner_name.lower()
        telecom_keywords = ['o2', 'telekom', 'slovak telekom', 't-mobile', 'orange']
        return any(keyword in name_lower for keyword in telecom_keywords)

    def _get_move_pdf_label(self, move):
        """Return a stable human-readable label for PDF bookmarks/banners."""
        move_name = move.name or move.ref or str(move.id)
        partner_name = move.partner_id.name or "N/A"
        return f"{move_name} | {partner_name}"

    def _get_move_pdf_banner_label(self, move):
        """Return a compact label for the visible page banner."""
        move_name = move.name or move.ref or str(move.id)
        partner_name = move.partner_id.name or ""
        if partner_name:
            return f"{move_name} | {partner_name}"
        return move_name

    def _get_move_pdf_sort_key(self, move):
        """Return a sort key that never mixes booleans with strings."""
        move_date = move.invoice_date or move.date or fields.Date.today()
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

    def _prepare_pdf_reader(self, pdf_data, attachment, banner_text):
        """Return a PdfReader for the attachment, optionally with a page banner."""
        pdf_stream = io.BytesIO(pdf_data)

        if self.include_page_banner:
            try:
                pdf_stream = self._add_page_banner_to_pdf(pdf_stream, banner_text)
            except Exception as banner_err:
                _logger.warning(
                    "Could not add banner to %s: %s", attachment.name, banner_err
                )
                pdf_stream.seek(0)

        reader = PdfReader(pdf_stream, strict=False)
        if getattr(reader, "is_encrypted", False):
            reader.decrypt("")
        return reader

    def _merge_pdfs(self, moves_with_attachments):
        """Merge multiple PDF attachments into one.
        
        For O2/Telekom invoices, only the first page is included.
        Each invoice gets a PDF outline entry for identification. Page banners
        can be disabled from the wizard when maximum speed is preferred.
        """
        if not PdfWriter:
            raise UserError(_("PyPDF2 library is not installed. Please install it to use this feature."))
        
        writer = PdfWriter()
        successful_merges = 0
        errors = []
        page_count = 0
        
        for move in moves_with_attachments:
            attachment = move.message_main_attachment_id
            try:
                pdf_data = self._get_attachment_pdf_data(attachment)
                if not pdf_data:
                    errors.append(f"Empty attachment data: {attachment.name}")
                    continue

                outline_text = self._get_move_pdf_label(move)
                banner_text = self._get_move_pdf_banner_label(move)
                reader = self._prepare_pdf_reader(pdf_data, attachment, banner_text)
                source_page_count = len(reader.pages)

                if source_page_count == 0:
                    errors.append(f"Empty PDF: {attachment.name}")
                    continue

                is_telecom = self._is_telecom_vendor(move.partner_id.name)
                pages_to_copy = range(1) if is_telecom else range(source_page_count)
                first_output_page = page_count

                for page_index in pages_to_copy:
                    writer.add_page(reader.pages[page_index])
                    page_count += 1

                self._add_pdf_outline_item(writer, outline_text, first_output_page)
                successful_merges += 1

                if is_telecom:
                    _logger.info(
                        "Added first page only from %s (telecom vendor: %s)",
                        attachment.name,
                        move.partner_id.name,
                    )
                    
            except Exception as e:
                errors.append(f"Error with {attachment.name}: {str(e)}")
                _logger.warning("Failed to merge PDF %s: %s", attachment.name, e)
        
        if successful_merges == 0:
            raise UserError(_("No valid PDFs could be merged. Errors:\n%s") % "\n".join(errors))
        
        # Write merged PDF to bytes
        output_stream = io.BytesIO()
        writer.write(output_stream)
        merged_data = output_stream.getvalue()
        
        return merged_data, successful_merges, errors, page_count

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

    def action_generate_merged_pdf(self):
        """Generate a single merged PDF from all selected vendor bills."""
        self.ensure_one()
        
        if not self.move_ids:
            raise UserError(_("No invoices selected."))
        
        if self.attachment_count == 0:
            raise UserError(_("None of the selected invoices have PDF attachments."))
        
        self.write({"state": "processing", "progress": 0})
        
        try:
            # Get all moves with attachments, sorted by date/name for consistent ordering
            moves_with_attachments = self.move_ids.filtered(
                lambda m: m.message_main_attachment_id
                and m.message_main_attachment_id.mimetype == "application/pdf"
            ).sorted(key=self._get_move_pdf_sort_key)
            
            _logger.info(f"Starting PDF merge for {len(moves_with_attachments)} invoices")
            
            # Merge all PDFs (passes moves so we can check vendor for first-page-only logic)
            merged_pdf_data, successful_count, errors, page_count = self._merge_pdfs(moves_with_attachments)
            
            # Create the result attachment
            filename = f"vendor_bills_merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            attachment = self.env["ir.attachment"].create({
                "name": filename,
                "datas": base64.b64encode(merged_pdf_data),
                "mimetype": "application/pdf",
                "res_model": "vendor.bill.merged.pdf",
                "res_id": 0,  # Will be updated after creating the record
            })
            
            # Create merged PDF record for later access
            merged_pdf_name = f"Merged Bills {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            merged_pdf_record = self.env["vendor.bill.merged.pdf"].create({
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
            
            _logger.info(
                "Successfully created merged PDF: %s (%s invoices, %s pages)",
                filename,
                successful_count,
                page_count,
            )
            
            # Stay on the wizard so user can download manually (more robust for connection issues)
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
