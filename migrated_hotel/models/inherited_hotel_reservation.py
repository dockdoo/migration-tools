# Copyright 2019  Pablo Q. Barriuso
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, fields, api


class HotelReservation(models.Model):

    _inherit = 'hotel.reservation'

    remote_id = fields.Integer(require=True, copy=False, readonly=True,
            help="ID of the target record in the previous version")

    @api.model
    def create(self, vals):
        reservation_id = super().create(vals)
        return reservation_id

    @api.multi
    def write(self, vals):
        ret = super().write(vals)
        return ret