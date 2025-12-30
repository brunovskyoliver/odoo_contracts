# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from openupgrade import openupgrade_41_0 as ou


def migrate(env, version):
    """
    Migrate to version 18.0.1.4.4
    - Add new fields to res_company for quantity alerts
    - Add new model product.quantity.alert
    """
    
    # Add new fields to res_company table if they don't exist
    env.cr.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'res_company'
            AND column_name = 'quantity_alert_email'
        )
    """)
    
    if not env.cr.fetchone()[0]:
        env.cr.execute("""
            ALTER TABLE res_company
            ADD COLUMN quantity_alert_email varchar
        """)
        env.cr.commit()
    
    # Add new fields to product_template table if they don't exist
    env.cr.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'product_template'
            AND column_name = 'alert_qty_enabled'
        )
    """)
    
    if not env.cr.fetchone()[0]:
        env.cr.execute("""
            ALTER TABLE product_template
            ADD COLUMN alert_qty_enabled boolean DEFAULT false
        """)
        env.cr.commit()
    
    env.cr.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'product_template'
            AND column_name = 'minimum_alert_qty'
        )
    """)
    
    if not env.cr.fetchone()[0]:
        env.cr.execute("""
            ALTER TABLE product_template
            ADD COLUMN minimum_alert_qty integer DEFAULT 2
        """)
        env.cr.commit()
    
    env.cr.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'product_template'
            AND column_name = 'alert_frequency_per_week'
        )
    """)
    
    if not env.cr.fetchone()[0]:
        env.cr.execute("""
            ALTER TABLE product_template
            ADD COLUMN alert_frequency_per_week integer DEFAULT 1
        """)
        env.cr.commit()
