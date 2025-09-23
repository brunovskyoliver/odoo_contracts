from odoo import http

class DebugController(http.Controller):
    @http.route('/debug_headers', type='http', auth='public')
    def debug_headers(self, **kw):
        from odoo.http import request
        return str(request.httprequest.headers)
