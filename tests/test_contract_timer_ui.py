# Copyright 2026
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestContractTimerUi(HttpCase):
    def test_timer_systray_tour(self):
        self.start_tour("/odoo", "contract_timer_systray_tour", login="admin")
