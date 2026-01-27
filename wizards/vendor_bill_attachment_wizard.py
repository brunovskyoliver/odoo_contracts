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

    def _merge_pdfs(self, moves_with_attachments):
        """Merge multiple PDF attachments into one.
        
        For O2/Telekom invoices, only the first page is included.
        Each PDF gets a banner with the invoice reference for identification.
        """
        if not PdfMerger:
            raise UserError(_("PyPDF2 library is not installed. Please install it to use this feature."))
        
        merger = PdfMerger()
        successful_merges = 0
        errors = []
        
        for move in moves_with_attachments:
            attachment = move.message_main_attachment_id
            try:
                pdf_data = base64.b64decode(attachment.datas)
                pdf_stream = io.BytesIO(pdf_data)
                
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
                        _logger.warning(f"Could not add banner to {attachment.name}: {banner_err}")
                        pdf_stream.seek(0)
                        pdf_to_merge = pdf_stream
                    
                    # Check if this is a telecom vendor (O2, Telekom) - only first page
                    is_telecom = self._is_telecom_vendor(move.partner_id.name)
                    
                    if is_telecom:
                        # Only append first page for telecom vendors
                        merger.append(pdf_to_merge, pages=(0, 1))
                        _logger.info(f"Added first page only from {attachment.name} (telecom vendor: {move.partner_id.name})")
                    else:
                        # Append all pages for other vendors
                        merger.append(pdf_to_merge)
                    
                    successful_merges += 1
                else:
                    errors.append(f"Empty PDF: {attachment.name}")
                    
            except Exception as e:
                errors.append(f"Error with {attachment.name}: {str(e)}")
                _logger.warning(f"Failed to merge PDF {attachment.name}: {e}")
        
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
            ).sorted(key=lambda m: (m.invoice_date or m.date or fields.Date.today(), m.name))
            
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
            
            _logger.info(f"Successfully created merged PDF: {filename} ({successful_count} pages merged)")
            
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
