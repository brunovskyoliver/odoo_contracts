# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class VendorBillMergedPdf(models.Model):
    """Store merged vendor bill PDFs for later access."""

    _name = "vendor.bill.merged.pdf"
    _description = "Merged Vendor Bill PDF"
    _order = "create_date desc"

    name = fields.Char(
        string="Name",
        required=True,
        readonly=True,
    )
    attachment_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="PDF File",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    file_data = fields.Binary(
        string="PDF",
        related="attachment_id.datas",
        readonly=True,
    )
    file_name = fields.Char(
        string="File Name",
        related="attachment_id.name",
        readonly=True,
    )
    file_size = fields.Integer(
        string="File Size (bytes)",
        related="attachment_id.file_size",
        readonly=True,
    )
    invoice_count = fields.Integer(
        string="Invoice Count",
        readonly=True,
    )
    page_count = fields.Integer(
        string="Page Count",
        readonly=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Created By",
        default=lambda self: self.env.user,
        readonly=True,
    )
    move_ids = fields.Many2many(
        comodel_name="account.move",
        string="Source Invoices",
        readonly=True,
    )
    notes = fields.Text(
        string="Notes",
        help="Any notes or errors during generation",
    )

    def action_download(self):
        """Download the merged PDF."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.attachment_id.id}?download=true",
            "target": "new",
        }

    def action_view_invoices(self):
        """View the source invoices."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Source Invoices",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.move_ids.ids)],
            "context": {"create": False},
        }

    @api.ondelete(at_uninstall=False)
    def _unlink_attachment(self):
        """Delete the attachment when the record is deleted."""
        self.mapped("attachment_id").unlink()
