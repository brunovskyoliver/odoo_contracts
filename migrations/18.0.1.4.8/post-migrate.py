# Copyright 2026 NOVEM IT
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).


def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_model_fields
           SET copied = false
         WHERE model = 'account.move'
           AND name = 'x_invoice_sent'
        """
    )

    cr.execute(
        """
        UPDATE ir_cron
           SET active = false
         WHERE id = 80
           AND COALESCE(cron_name, '') = 'SEND INVOICES'
        """
    )

    cr.execute(
        """
        UPDATE account_move move
           SET x_invoice_sent = false,
               contract_customer_mail_state = 'pending',
               contract_customer_mail_failure_reason = NULL
         WHERE move.move_type = 'out_refund'
           AND move.state = 'posted'
           AND COALESCE(move.x_invoice_sent, false) = true
           AND COALESCE(move.is_move_sent, false) = false
           AND NOT EXISTS (
                SELECT 1
                 FROM mail_message message
                 WHERE message.model = 'account.move'
                   AND message.res_id = move.id
                   AND message.message_type IN ('email', 'email_outgoing')
           )
        """
    )
