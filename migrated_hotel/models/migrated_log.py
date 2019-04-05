# Copyright 2019  Pablo Q. Barriuso
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, fields


class MigrateLog(models.Model):
    _name = 'migrated.log'

    name = fields.Char('Message')
    date_time = fields.Datetime()
    migrated_hotel_id = fields.Many2one('migrated.hotel')
    type = fields.Selection([
        ('partner', 'Partners'),
        ('product', 'Products'),
        ('folio', 'Folio')
    ])
