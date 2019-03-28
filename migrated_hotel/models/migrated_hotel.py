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

    @api.multi
    def action_migrate_res_partners(self):
        self.ensure_one()
        try:
            noderpc = odoorpc.ODOO(self.odoo_host, self.odoo_protocol, self.odoo_port)
            noderpc.login(self.odoo_db, self.odoo_user, self.odoo_password)
        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)

        try:
            # import remote partners without contacts (parent_id is not set)
            remote_partner_ids = noderpc.env['res.partner'].search([
                ('parent_id', '=', False),
            ])
            for remote_res_partner_id in remote_partner_ids:
                rpc_res_partner = noderpc.env['res.partner'].browse(remote_res_partner_id)
                # prepare some related fields
                country_id = self.env['res.country'].search([
                    ('code', '=', rpc_res_partner.country_id.code)
                ]).id or None
                state_id = self.env['res.country.state'].search([
                    ('country_id', '=', country_id),
                    ('code', '=', rpc_res_partner.state_id.code)
                ]).id or None
                category_id = self.env['res.partner.category'].search([
                    ('name', '=', rpc_res_partner.category_id.name),
                    ('parent_id.name', '=', rpc_res_partner.category_id.parent_id.name),
                ]).id or None
                import wdb; wdb.set_trace()
                migrated_res_partner = self.env['res.partner'].create({
                    'lastname': rpc_res_partner.lastname,
                    'firstname': rpc_res_partner.firstname,
                    'phone': rpc_res_partner.phone,
                    'mobile': rpc_res_partner.mobile,
                    # Odoo 11 unknown fields: fax
                    'email': rpc_res_partner.email,
                    'website': rpc_res_partner.website,
                    'lang': rpc_res_partner.lang,
                    'is_company': rpc_res_partner.is_company,
                    'type': rpc_res_partner.type,
                    'street': rpc_res_partner.street,
                    'street2': rpc_res_partner.street2,
                    'zip_id': rpc_res_partner.zip_id.id,
                    'zip': rpc_res_partner.zip,
                    'city': rpc_res_partner.city,
                    'state_id': state_id,
                    'country_id': country_id,
                    'comment': rpc_res_partner.comment,
                    'document_type': rpc_res_partner.documenttype,
                    'document_number': rpc_res_partner.poldocument,
                    'document_expedition_date': rpc_res_partner.polexpedition,
                    'gender': rpc_res_partner.gender,
                    'birthdate_date': rpc_res_partner.birthdate_date,
                    'code_ine_id': rpc_res_partner.code_ine.id,
                    'category_id': category_id,
                    'unconfirmed': rpc_res_partner.unconfirmed,
                })
                migrated_res_partner.remote_id = remote_res_partner_id

                _logger.info('User #%s migrated res.partner with ID: [%s, %s]',
                                 self._context.get('uid'), migrated_res_partner.id, remote_res_partner_id)

        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)
