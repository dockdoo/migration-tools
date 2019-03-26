# Copyright 2019  Pablo Q. Barriuso
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
import urllib
import odoorpc.odoo
from odoo.exceptions import ValidationError
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class MigratedHotel(models.Model):
    _name = 'migrated.hotel'

    name = fields.Char('Name')
    odoo_host = fields.Char('Host', required=True, help='Full URL to the host.')
    odoo_db = fields.Char('Database Name', help='Odoo database name.')
    odoo_user = fields.Char('Username', help='Odoo administration user.')
    odoo_password = fields.Char('Password', help='Odoo password.')
    odoo_port = fields.Integer(string='TCP Port', default=443,
                               help='Specify the TCP port for the XML-RPC protocol.')
    odoo_protocol = fields.Selection([('jsonrpc+ssl', 'jsonrpc+ssl')],
                                     'Protocol', required=True, default='jsonrpc+ssl')
    odoo_version = fields.Char()

    @api.model
    def create(self, vals):
        try:
            noderpc = odoorpc.ODOO(vals['odoo_host'], vals['odoo_protocol'], vals['odoo_port'])
            noderpc.login(vals['odoo_db'], vals['odoo_user'], vals['odoo_password'])

            vals.update({'odoo_version': noderpc.version})

        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)
        else:
            hotel_id = super().create(vals)
            noderpc.logout()
            return hotel_id

    @api.multi
    def action_synchronize_res_users(self):
        self.ensure_one()
        try:
            noderpc = odoorpc.ODOO(self.odoo_host, self.odoo_protocol, self.odoo_port)
            noderpc.login(self.odoo_db, self.odoo_user, self.odoo_password)
        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)

        try:
            # synchronize remote users
            remote_user_ids = noderpc.env['res.users'].search([
                ('login', 'not in', ['admin', 'manager', 'recepcion'])
            ])
            for remote_res_user_id in remote_user_ids:
                rpc_res_user = noderpc.env['res.users'].browse(remote_res_user_id)
                res_user = self.env['res.users'].search([
                    ('login', '=', rpc_res_user.login)
                ])
                if res_user:
                    res_user.partner_id.remote_id = rpc_res_user.partner_id.id
                    _logger.info('User #%s updated a res.partner ID: [%s] with remote_id: [%s]',
                                 self._context.get('uid'), res_user.partner_id.id, res_user.partner_id.remote_id)
                else:
                    _logger.warning('User #%s ignored migration of remote res.users ID: [%s]. '
                                    'The user does not exist in this database',
                                 self._context.get('uid'), remote_res_user_id)

        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)

    @api.multi
    def action_synchronize_res_users_3x_performance(self):
        self.ensure_one()
        import wdb; wdb.set_trace()
        try:
            noderpc = odoorpc.ODOO(self.odoo_host, self.odoo_protocol, self.odoo_port)
            noderpc.login(self.odoo_db, self.odoo_user, self.odoo_password)
        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)

        try:
            # synchronize remote users
            remote_user_ids = noderpc.env['res.users'].search([
                ('login', 'not in', ['admin', 'manager', 'recepcion'])
            ])
            # TEST IN PRODUCTION ENVIRONMENT: improve performance 3.5x
            for remote_res_user_id in remote_user_ids:
                rpc_res_user = noderpc.env['res.users'].search_read(
                    [('id', '=', remote_res_user_id)],
                    ['id', 'login', 'partner_id']
                )[0]
                res_user = self.env['res.users'].search([
                    ('login', '=', rpc_res_user['login'])
                ])
                if res_user:
                    res_user.partner_id.remote_id = rpc_res_user['partner_id'][0]
                    _logger.info('User #%s updated a res.partner ID: [%s] with remote_id: [%s]',
                                 self._context.get('uid'), res_user.partner_id.id, res_user.partner_id.remote_id)
                else:
                    _logger.warning('User #%s ignored migration of remote res.users ID: [%s]. '
                                    'The user does not exist in this database',
                                    self._context.get('uid'), remote_res_user_id)

        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)

