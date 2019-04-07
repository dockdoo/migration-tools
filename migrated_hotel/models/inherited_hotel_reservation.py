# Copyright 2019  Pablo Q. Barriuso
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, fields, api


class HotelReservation(models.Model):

    _inherit = 'hotel.reservation'

    remote_id = fields.Integer(require=True, copy=False, readonly=True,
            help="ID of the target record in the previous version")

    @api.multi
    def confirm(self):
        if self._context.get('tracking_disable'):
            return True
        return super().confirm()