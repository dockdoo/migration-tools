# Copyright 2019  Pablo Q. Barriuso
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import time
import logging
import urllib
import odoorpc.odoo
from odoo.exceptions import ValidationError, UserError
from odoo import models, fields, api
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT

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

    migration_date_d = fields.Date('Migration D-date')
    log_ids = fields.One2many('migrated.log', 'migrated_hotel_id')

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
    def _prepare_partner_remote_data(self, rpc_res_partner, country_map_ids,
                             country_state_map_ids, category_map_ids):
        # prepare country_id related field
        remote_id = rpc_res_partner['country_id'] and rpc_res_partner['country_id'][0]
        country_id = remote_id and country_map_ids.get(remote_id) or None
        # prepare state_id related field
        remote_id = rpc_res_partner['state_id'] and rpc_res_partner['state_id'][0]
        state_id = remote_id and country_state_map_ids.get(remote_id) or None
        # prepare category_ids related field
        remote_ids = rpc_res_partner['category_id'] and rpc_res_partner['category_id']
        category_ids = remote_ids and [category_map_ids.get(r) for r in remote_ids] or None
        # prepare parent_id related field
        parent_id = rpc_res_partner['parent_id']
        VAT =  rpc_res_partner['vat']
        if parent_id:
            res_partner = self.env['res.partner'].search([
                ('remote_id', '=', parent_id[0])
            ])
            parent_id = res_partner.id
            VAT = res_partner.vat
        # TODO: prepare child_ids related field
        return {
            'lastname': rpc_res_partner['lastname'],
            'firstname': rpc_res_partner['firstname'],
            'phone': rpc_res_partner['phone'],
            'mobile': rpc_res_partner['mobile'],
            # Odoo 11 unknown field: fax
            'email': rpc_res_partner['email'],
            'website': rpc_res_partner['website'],
            'lang': rpc_res_partner['lang'],
            'is_company': rpc_res_partner['is_company'],
            'type': rpc_res_partner['type'],
            'street': rpc_res_partner['street'],
            'street2': rpc_res_partner['street2'],
            # 'zip_id': rpc_res_partner['zip_id'] and rpc_res_partner['zip_id'][0],
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
            'parent_id': parent_id,
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
                try:
                    rpc_res_partner = noderpc.env['res.partner'].search_read(
                        [('id', '=', remote_res_partner_id)],
                    )[0]
                    vals = self._prepare_partner_remote_data(
                        rpc_res_partner,
                        country_map_ids,
                        country_state_map_ids,
                        category_map_ids,
                    )
                    migrated_res_partner = self.env['res.partner'].create(vals)
                    migrated_res_partner.remote_id = remote_res_partner_id

                    _logger.info('User #%s migrated res.partner with ID [local, remote]: [%s, %s]',
                                     self._context.get('uid'), migrated_res_partner.id, remote_res_partner_id)

                except Exception as err:
                    migrated_log = self.env['migrated.log'].create({
                        'name': err,
                        'date_time': fields.Datetime.now(),
                        'migrated_hotel_id': self.id,
                        'type': 'partner'
                    })
                    _logger.error('ERROR migrating remote res.partner with ID remote: [%s] with ERROR LOG [%s]: (%s)',
                                  remote_res_partner_id, migrated_log.id, err)
                    continue

            # Second, import remote partners with contacts (already created in the previous step)
            _logger.info("Migrating 'res.partners' with parent_id...")
            remote_partner_ids = noderpc.env['res.partner'].search([
                ('id', 'in', remote_partner_set_ids),
                ('parent_id', '!=', False),
                ('user_ids', '=', False),
            ])
            for remote_res_partner_id in remote_partner_ids:
                try:
                    rpc_res_partner = noderpc.env['res.partner'].search_read(
                        [('id', '=', remote_res_partner_id)],
                    )[0]
                    vals = self._prepare_partner_remote_data(
                        rpc_res_partner,
                        country_map_ids,
                        country_state_map_ids,
                        category_map_ids,
                    )
                    migrated_res_partner = self.env['res.partner'].create(vals)
                    migrated_res_partner.remote_id = remote_res_partner_id

                    _logger.info('User #%s migrated res.partner with ID [local, remote]: [%s, %s]',

                                     self._context.get('uid'), migrated_res_partner.id, remote_res_partner_id)
                except Exception as err:
                    migrated_log = self.env['migrated.log'].create({
                        'name': err,
                        'date_time': fields.Datetime.now(),
                        'migrated_hotel_id': self.id,
                        'type': 'partner'
                    })
                    _logger.error('ERROR migrating remote res.partner with ID remote: [%s] with ERROR LOG [%s]: (%s)',
                                  remote_res_partner_id, migrated_log.id, err)
                    continue

            time_migration_partners = (time.time() - start_time) / 60
            _logger.info('action_migrate_res_partners elapsed time: %s minutes',
                         time_migration_partners)

        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)
        else:
            noderpc.logout()

    @api.multi
    def action_migrate_products(self):
        start_time = time.time()
        self.ensure_one()
        try:
            noderpc = odoorpc.ODOO(self.odoo_host, self.odoo_protocol, self.odoo_port)
            noderpc.login(self.odoo_db, self.odoo_user, self.odoo_password)
        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)

        try:
            # prepare products of interest
            _logger.info("Preparing 'product.product' of interest...")
            hotel_room_type_ids = noderpc.env['hotel.virtual.room'].search_read(
                [],
                ['product_id']
            )
            hotel_room_type_set = [x['product_id'][0] for x in hotel_room_type_ids]
            hotel_room_ids = noderpc.env['hotel.room'].search_read(
                [],
                ['product_id']
            )
            hotel_room_set = [x['product_id'][0] for x in hotel_room_ids]
            hotel_room_amenities_ids = noderpc.env['hotel.room.amenities'].search_read(
                [],
                ['product_tmpl_id']
            )
            hotel_room_amenities_set = [x['product_tmpl_id'][0] for x in hotel_room_amenities_ids]

            # set of remote products of NO interest
            remote_products_set_ids = list(set().union(
                hotel_room_type_set,
                hotel_room_set,
                hotel_room_amenities_set
            ))
            # First, import remote partners without contacts (parent_id is not set)
            _logger.info("Migrating 'product.product'...")
            remote_product_ids = noderpc.env['product.product'].search([
                ('id', 'not in', remote_products_set_ids),
            ])
            for remote_product_id in remote_product_ids:
                try:
                    rpc_product = noderpc.env['product.product'].search_read(
                        [('id', '=', remote_product_id)],
                    )[0]
                    migrated_product = self.env['product.template'].search([
                        ('remote_id', '=', remote_product_id)
                    ]) or None
                    if not migrated_product:
                        vals = {
                            'name': rpc_product['name'],
                            'list_price': rpc_product['list_price'],
                            'taxes_id': rpc_product['taxes_id'] and [[6, False, rpc_product['taxes_id']]] or None,
                            'type': 'service',
                            'sale_ok': True,
                            'purchase_ok': False,
                        }
                        migrated_product = self.env['product.template'].create(vals)
                    #
                    migrated_product.remote_id = remote_product_id

                    _logger.info('User #%s migrated product.product with ID [local, remote]: [%s, %s]',

                                 self._context.get('uid'), migrated_product.id, remote_product_id)
                except Exception as err:
                    migrated_log = self.env['migrated.log'].create({
                        'name': err,
                        'date_time': fields.Datetime.now(),
                        'migrated_hotel_id': self.id,
                        'type': 'partner'
                    })
                    _logger.error('ERROR migrating remote product.product with ID remote: [%s] with ERROR LOG [%s]: (%s)',
                                  remote_product_id, migrated_log.id, err)
                    continue

            time_migration_products = (time.time() - start_time) / 60
            _logger.info('action_migrate_products elapsed time: %s minutes',
                         time_migration_products)

        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)
        else:
            noderpc.logout()

    @api.multi
    def _prepare_folio_remote_data(self, rpc_hotel_folio, res_users_map_ids, category_map_ids,
                                   room_type_map_ids, room_map_ids, noderpc):
        # prepare partner_id related field
        default_res_partner = self.env['res.partner'].search([
            ('user_ids', 'in', self._context.get('uid'))
        ])
        remote_id = rpc_hotel_folio['partner_id'] and rpc_hotel_folio['partner_id'][0]
        res_partner = self.env['res.partner'].search([
            ('remote_id', '=', remote_id)
        ]) or default_res_partner
        remote_id = rpc_hotel_folio['partner_invoice_id'] and rpc_hotel_folio['partner_invoice_id'][0]
        res_partner_invoice = self.env['res.partner'].search([
            ('remote_id', '=', remote_id)
        ]) or default_res_partner.company_id
        remote_id = rpc_hotel_folio['user_id'] and rpc_hotel_folio['user_id'][0]
        res_user_id = remote_id and res_users_map_ids.get(remote_id)
        # prepare category_ids related field
        remote_ids = rpc_hotel_folio['segmentation_id'] and rpc_hotel_folio['segmentation_id']
        category_ids = remote_ids and [category_map_ids.get(r) for r in remote_ids] or None
        # prepare default state value
        state = 'confirm'
        if rpc_hotel_folio['state'] != 'sale':
            state = rpc_hotel_folio['state']

        vals = {
            'name': rpc_hotel_folio['name'],
            'partner_id': res_partner.id,
            'partner_invoice_id': res_partner_invoice.id,
            'segmentation_ids': category_ids and [[6, False, category_ids]] or None,
            'reservation_type': rpc_hotel_folio['reservation_type'],
            'channel_type': rpc_hotel_folio['channel_type'],
            'customer_notes': rpc_hotel_folio['wcustomer_notes'],
            'internal_comment': rpc_hotel_folio['internal_comment'],
            'state': state,
            'cancelled_reason': rpc_hotel_folio['cancelled_reason'],
            'date_order': rpc_hotel_folio['date_order'],
            'user_id': res_user_id,
            '__last_update': rpc_hotel_folio['__last_update'],
        }
        # prepare room_lines related field
        remote_ids = rpc_hotel_folio['room_lines'] and rpc_hotel_folio['room_lines']
        hotel_reservations = noderpc.env['hotel.reservation'].search_read(
            [('id', 'in', remote_ids)],
        )
        room_lines_cmds = []
        for room in hotel_reservations:
            # 'web sale' reservations after D-date are __not__ migrated with this script
            if room['channel_type'] == 'web' and fields.Date.from_string(
                    room['checkin']) >= fields.Date.from_string(self.migration_date_d):
                continue

            remote_ids = room['reservation_lines'] and room['reservation_lines']
            hotel_reservation_lines = noderpc.env['hotel.reservation.line'].search_read(
                [('id', 'in', remote_ids)],
                ['date', 'price']
            )
            reservation_line_cmds = []
            for reservation_line in hotel_reservation_lines:
                reservation_line_cmds.append((0, False, {
                    'date': reservation_line['date'],
                    'price': reservation_line['price'],
                }))
            # prepare hotel_room_type related field
            remote_id = room['virtual_room_id'] and room['virtual_room_id'][0]
            room_type_id = remote_id and room_type_map_ids.get(remote_id) or None
            # prepare hotel_room related field
            remote_id = room['product_id'] and room['product_id'][0]
            room_id = remote_id and room_map_ids.get(remote_id) or None
            # prepare hotel.folio.room_lines
            room_lines_cmds.append((0, False, {
                'room_type_id': room_type_id,
                'room_id': room_id,
                'checkin': fields.Date.from_string(room['checkin']).strftime(
                    DEFAULT_SERVER_DATE_FORMAT),
                'checkout': fields.Date.from_string(room['checkout']).strftime(
                    DEFAULT_SERVER_DATE_FORMAT),
                'state': room['state'],
                'cancelled_reason': room['cancelled_reason'],
                'out_service_description': room['out_service_description'],
                'adults': room['adults'],
                'children': room['children'],
                'reservation_line_ids': reservation_line_cmds,
            }))
            vals.update({'room_lines': room_lines_cmds})

            # 'direct sale' reservations after D-date are migrated with no products
            if room['channel_type'] != 'web' and fields.Date.from_string(
                    room['checkin']) >= fields.Date.from_string(self.migration_date_d):
                continue

            # reservations before D-date are migrated with Odoo 10 products

        return vals

    @api.multi
    def action_migrate_reservation(self):
        start_time = time.time()
        self.ensure_one()

        if not self.migration_date_d:
            raise ValidationError('Set a Migration D-date before proceed.')

        try:
            noderpc = odoorpc.ODOO(self.odoo_host, self.odoo_protocol, self.odoo_port)
            noderpc.login(self.odoo_db, self.odoo_user, self.odoo_password)
        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)

        try:
            # prepare res.users ids
            _logger.info("Mapping local with remote 'res.users' ids...")
            remote_ids = noderpc.env['res.users'].search([])
            remote_records = noderpc.env['res.users'].browse(remote_ids)
            res_users_map_ids = {}
            for record in remote_records:
                res_users_id = self.env['res.users'].search([
                    ('login', '=', record.login),
                ]).id or self._context.get('uid')
                res_users_map_ids.update({record.id: res_users_id})
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
            # prepare hotel.room.type ids
            _logger.info("Mapping local with remote 'hotel.room.type' ids...")
            remote_ids = noderpc.env['hotel.virtual.room'].search([])
            remote_xml_ids = noderpc.env['hotel.virtual.room'].browse(
                remote_ids).get_external_id()
            room_type_map_ids = {}
            for key, value in remote_xml_ids.items():
                room_type_id = self.env['ir.model.data'].xmlid_to_res_id(value)
                room_type_map_ids.update({int(key): room_type_id})
            # prepare hotel.room ids
            _logger.info("Mapping local with remote 'hotel.room' ids...")
            remote_ids = noderpc.env['hotel.room'].search([])
            remote_hotel_rooms = noderpc.env['hotel.room'].browse(remote_ids)
            room_map_ids = {}
            # TODO: may be improved with search_read product_id ?
            for remote_hotel_room in remote_hotel_rooms:
                remote_xml_id = remote_hotel_room.get_external_id()
                value = list(remote_xml_id.values())[0]
                room_id = self.env['ir.model.data'].xmlid_to_res_id(value)
                room_map_ids.update({remote_hotel_room.product_id.id: room_id})

            # prepare reservation of interest
            _logger.info("Preparing 'hotel.folio' of interest...")
            remote_hotel_folio_ids = noderpc.env['hotel.folio'].search([])
            _logger.info("Migrating 'hotel.folio'...")
            for remote_hotel_folio_id in remote_hotel_folio_ids:
                try:
                    rpc_hotel_folio = noderpc.env['hotel.folio'].search_read(
                        [('id', '=', remote_hotel_folio_id)],
                    )[0]
                    migrated_hotel_folio = self.env['hotel.folio'].search([
                        ('remote_id', '=', remote_hotel_folio_id)
                    ]) or None
                    if not migrated_hotel_folio:
                        vals = self._prepare_folio_remote_data(rpc_hotel_folio,
                                                               res_users_map_ids,
                                                               category_map_ids,
                                                               room_type_map_ids,
                                                               room_map_ids,
                                                               noderpc)
                        migrated_hotel_folio = self.env['hotel.folio'].create(vals)
                    #
                    migrated_hotel_folio.remote_id = remote_hotel_folio_id

                    _logger.info('User #%s migrated hotel.folio with ID [local, remote]: [%s, %s]',

                                 self._context.get('uid'), migrated_hotel_folio.id, remote_hotel_folio_id)
                except Exception as err:
                    migrated_log = self.env['migrated.log'].create({
                        'name': err,
                        'date_time': fields.Datetime.now(),
                        'migrated_hotel_id': self.id,
                        'type': 'folio'
                    })
                    _logger.error('ERROR migrating remote hotel.folio with ID remote: [%s] with ERROR LOG [%s]: (%s)',
                                  remote_hotel_folio_id, migrated_log.id, err)
                    continue

            time_migration_products = (time.time() - start_time) / 60
            _logger.info('action_migrate_reservation elapsed time: %s minutes',
                         time_migration_products)

        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)
        else:
            noderpc.logout()

    @api.multi
    def action_clean_up(self):
        start_time = time.time()
        self.ensure_one()
        # disable Odoo 10 products
        # disable specific closure_reason created for migration
        time_migration_partners = (time.time() - start_time) / 60
        _logger.info('action_clean_up elapsed time: %s minutes',
                     time_migration_partners)