# Copyright 2019  Pablo Q. Barriuso
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import time
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
    def _prepare_remote_data(
            self,
            rpc_res_partner,
            country_map_ids,
            country_state_map_ids,
            category_map_ids):
        # prepare country related fields
        remote_id = rpc_res_partner['country_id'] and rpc_res_partner['country_id'][0]
        country_id = remote_id and country_map_ids.get(remote_id) or None
        remote_id = rpc_res_partner['state_id'] and rpc_res_partner['state_id'][0]
        state_id = remote_id and country_state_map_ids.get(remote_id) or None
        # prepare category related fields
        remote_ids = rpc_res_partner['category_id'] and rpc_res_partner['category_id']
        category_ids = remote_ids and [category_map_ids.get(r) for r in remote_ids] or None

        # use VAT of your parent_id
        VAT =  ''
        if not rpc_res_partner['parent_id']:
            VAT = rpc_res_partner['vat']
        else:
            # remote partners without parent_id are migrated first
            VAT = self.env['res.partner'].search([
                ('remote_id', '=', rpc_res_partner['parent_id'][0])
            ]).vat or None
        return {
            'lastname': rpc_res_partner['lastname'],
            'firstname': rpc_res_partner['firstname'],
            'phone': rpc_res_partner['phone'],
            'mobile': rpc_res_partner['mobile'],
            # Odoo 11 unknown fields: fax
            'email': rpc_res_partner['email'],
            'website': rpc_res_partner['website'],
            'lang': rpc_res_partner['lang'],
            'is_company': rpc_res_partner['is_company'],
            'type': rpc_res_partner['type'],
            'street': rpc_res_partner['street'],
            'street2': rpc_res_partner['street2'],
            'zip_id': rpc_res_partner['zip_id'] and rpc_res_partner['zip_id'][0],
            'zip': rpc_res_partner['zip'],
            'city': rpc_res_partner['city'],
            'state_id': state_id,
            'country_id': country_id,
            'comment': rpc_res_partner['comment'],
            'document_type': rpc_res_partner['documenttype'],
            'document_number': rpc_res_partner['poldocument'],
            'document_expedition_date': rpc_res_partner['polexpedition'],
            'gender': rpc_res_partner['gender'],
            'birthdate_date': rpc_res_partner['birthdate_date'],
            'code_ine_id': rpc_res_partner['code_ine'] and rpc_res_partner['code_ine'][0],
            'category_id': category_ids and [[6, False, category_ids]] or None,
            'unconfirmed': rpc_res_partner['unconfirmed'],
            'vat': VAT,
        }

    @api.multi
    def action_migrate_res_partners(self):
        start_time = time.time()
        self.ensure_one()
        try:
            noderpc = odoorpc.ODOO(self.odoo_host, self.odoo_protocol, self.odoo_port)
            noderpc.login(self.odoo_db, self.odoo_user, self.odoo_password)
        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)

        try:
            # prepare res.country ids
            _logger.info("Mapping local with remote 'res.country' ids...")
            remote_ids = noderpc.env['res.country'].search([])
            remote_xml_ids = noderpc.env['res.country'].browse(
                remote_ids).get_external_id()
            country_map_ids = {}
            for key, value in remote_xml_ids.items():
                # Known Issue: res.country base.an, base.nt, base.tp, base.yu, base.zr are not
                # migrated from Odoo version 10 to version 11
                res_country_id = self.env['ir.model.data'].xmlid_to_res_id(value)
                country_map_ids.update({int(key): res_country_id})

            # prepare res.country.state ids
            _logger.info("Mapping local with remote 'res.country.state' ids...")
            remote_ids = noderpc.env['res.country.state'].search([])
            remote_xml_ids = noderpc.env['res.country.state'].browse(
                remote_ids).get_external_id()
            country_state_map_ids = {}
            for key, value in remote_xml_ids.items():
                res_country_state_id = self.env['ir.model.data'].xmlid_to_res_id(value)
                country_state_map_ids.update({int(key): res_country_state_id})

            # prepare res.partner.category ids
            _logger.info("Mapping local with remote 'res.partner.category' ids...")
            remote_ids = noderpc.env['res.partner.category'].search([])
            remote_records = noderpc.env['res.partner.category'].browse(remote_ids)
            category_map_ids = {}
            for record in remote_records:
                res_partner_category_id = self.env['res.partner.category'].search([
                    ('name', '=', record.name),
                    ('parent_id.name', '=', record.parent_id.name),
                ]).id
                category_map_ids.update({record.id: res_partner_category_id})

            # prepare partners of interest
            _logger.info("Preparing 'res.partners' of interest...")
            folio_ids = noderpc.env['hotel.folio'].search_read(
                [('state', '!=', 'out')],
                ['partner_id']
            )
            partners_folios_set = [x['partner_id'][0] for x in folio_ids]
            cardex_ids = noderpc.env['cardex'].search_read(
                [],
                ['partner_id']
            )
            partners_cardex_set = [x['partner_id'][0] for x in cardex_ids]
            invoice_ids = noderpc.env['account.invoice'].search_read(
                [],
                ['partner_id']
            )
            partners_invoice_set = [x['partner_id'][0] for x in invoice_ids]
            # set of remote partners of interest
            remote_partner_set_ids = list(set().union(
                partners_folios_set,
                partners_cardex_set,
                partners_invoice_set
            ))
            # First, import remote partners without contacts (parent_id is not set)
            _logger.info("Migrating 'res.partners' without parent_id...")
            remote_partner_ids = noderpc.env['res.partner'].search([
                ('id', 'in', remote_partner_set_ids),
                ('parent_id', '=', False),
                ('user_ids', '=', False),
            ])
            for remote_res_partner_id in remote_partner_ids:
                rpc_res_partner = noderpc.env['res.partner'].search_read(
                    [('id', '=', remote_res_partner_id)],
                )[0]
                vals = self._prepare_remote_data(
                    rpc_res_partner,
                    country_map_ids,
                    country_state_map_ids,
                    category_map_ids,
                )
                migrated_res_partner = self.env['res.partner'].create(vals)
                migrated_res_partner.remote_id = remote_res_partner_id

                _logger.info('User #%s migrated res.partner with ID [local, remote]: [%s, %s]',
                                 self._context.get('uid'), migrated_res_partner.id, remote_res_partner_id)

            # Second, import remote partners with contacts (already created in the previous step)
            _logger.info("Migrating 'res.partners' with parent_id...")
            remote_partner_ids = noderpc.env['res.partner'].search([
                ('id', 'in', remote_partner_set_ids),
                ('parent_id', '!=', False),
                ('user_ids', '=', False),
            ])
            for remote_res_partner_id in remote_partner_ids:
                rpc_res_partner = noderpc.env['res.partner'].search_read(
                    [('id', '=', remote_res_partner_id)],
                )[0]
                vals = self._prepare_remote_data(
                    rpc_res_partner,
                    country_map_ids,
                    country_state_map_ids,
                    category_map_ids,
                )
                migrated_res_partner = self.env['res.partner'].create(vals)
                migrated_res_partner.remote_id = remote_res_partner_id

                _logger.info('User #%s migrated res.partner with ID [local, remote]: [%s, %s]',
                                 self._context.get('uid'), migrated_res_partner.id, remote_res_partner_id)

            time_migration_partners = (time.time() - start_time) / 60
            _logger.info('action_migrate_res_partners elapsed time: %s minutes',
                         time_migration_partners)

        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)
