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
    cron_ready = fields.Boolean(default=False)

    dummy_backend_id = fields.Many2one('channel.backend', require=True)
    dummy_closure_reason_id = fields.Many2one('room.closure.reason', require=True)

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
                                 self._uid, res_user.partner_id.id, res_user.partner_id.remote_id)
                else:
                    _logger.warning('User #%s ignored migration of remote res.users ID: [%s]. '
                                    'The user does not exist in this database',
                                    self._uid, remote_res_user_id)

        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)

    @api.multi
    def check_vat(self, VAT, country_id):
        res_partner = self.env['res.partner']
        # quick and partial off-line checksum validation
        check_func = res_partner.simple_vat_check
        #check with country code as prefix of the TIN
        vat_country, vat_number = res_partner._split_vat(VAT)
        if not check_func(vat_country, vat_number):
            #if fails, check with country code from country
            country_code = self.env['res.country'].browse(country_id).code
            if country_code:
                if not check_func(country_code.lower(), VAT):
                    return False
        return True

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
            parent_id = self.env['res.partner'].search([
                ('remote_id', '=', parent_id[0])
            ]).id
            VAT = ''

        comment = rpc_res_partner['comment'] or ''
        if VAT and not self.check_vat(VAT, country_id):
            check_vat_msg = 'Invalid VAT number ' + VAT + ' for this partner ' + rpc_res_partner['name']
            migrated_log = self.env['migrated.log'].create({
                'name': check_vat_msg,
                'date_time': fields.Datetime.now(),
                'migrated_hotel_id': self.id,
                'model': 'partner',
                'remote_id': rpc_res_partner['id'],
            })
            _logger.warning('Remote res.partner with ID remote: [%s] with ERROR LOG #%s: (%s)',
                          rpc_res_partner['id'], migrated_log.id, check_vat_msg)
            comment = check_vat_msg + "\n" + comment
            VAT = False

        # TODO: prepare child_ids related field
        return {
            'remote_id': rpc_res_partner['id'],
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
            'comment': comment,
            'document_type': rpc_res_partner['documenttype'],
            'document_number': rpc_res_partner['poldocument'],
            'document_expedition_date': rpc_res_partner['polexpedition'],
            'gender': rpc_res_partner['gender'],
            'birthdate_date': rpc_res_partner['birthdate_date'],
            'code_ine_id': rpc_res_partner['code_ine'] and rpc_res_partner['code_ine'][0],
            'category_id': category_ids and [[6, False, category_ids]] or None,
            'unconfirmed': True,
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
                [],
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
            # disable mail feature to speed-up migration
            context_no_mail = {
                'tracking_disable': True,
                'mail_notrack': True,
                'mail_create_nolog': True,
            }
            for remote_res_partner_id in remote_partner_ids:
                try:
                    migrated_res_partner = self.env['res.partner'].search([
                        ('remote_id', '=', remote_res_partner_id)
                    ]) or None

                    if not migrated_res_partner:
                        rpc_res_partner = noderpc.env['res.partner'].search_read(
                            [('id', '=', remote_res_partner_id)],
                        )[0]
                        vals = self._prepare_partner_remote_data(
                            rpc_res_partner,
                            country_map_ids,
                            country_state_map_ids,
                            category_map_ids,
                        )
                        migrated_res_partner = self.env['res.partner'].with_context(
                                context_no_mail
                            ).create(vals)

                        _logger.info('User #%s migrated res.partner with ID [local, remote]: [%s, %s]',
                                         self._uid, migrated_res_partner.id, remote_res_partner_id)

                except (ValueError, ValidationError, Exception) as err:
                    migrated_log = self.env['migrated.log'].create({
                        'name': err,
                        'date_time': fields.Datetime.now(),
                        'migrated_hotel_id': self.id,
                        'model': 'partner',
                        'remote_id': remote_res_partner_id,
                    })
                    _logger.error('Remote res.partner with ID remote: [%s] with ERROR LOG #%s: (%s)',
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
                    migrated_res_partner = self.env['res.partner'].search([
                        ('remote_id', '=', remote_res_partner_id)
                    ]) or None

                    if not migrated_res_partner:
                        rpc_res_partner = noderpc.env['res.partner'].search_read(
                            [('id', '=', remote_res_partner_id)],
                        )[0]
                        vals = self._prepare_partner_remote_data(
                            rpc_res_partner,
                            country_map_ids,
                            country_state_map_ids,
                            category_map_ids,
                        )
                        migrated_res_partner = self.env['res.partner'].with_context(
                                context_no_mail
                            ).create(vals)

                        _logger.info('User #%s migrated res.partner with ID [local, remote]: [%s, %s]',
                                         self._uid, migrated_res_partner.id, remote_res_partner_id)

                except (ValueError, ValidationError, Exception) as err:
                    migrated_log = self.env['migrated.log'].create({
                        'name': err,
                        'date_time': fields.Datetime.now(),
                        'migrated_hotel_id': self.id,
                        'model': 'partner',
                        'remote_id': remote_res_partner_id,
                    })
                    _logger.error('Remote res.partner with ID remote: [%s] with ERROR LOG #%s: (%s)',
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
            _logger.info("Migrating 'product.product'...")
            remote_product_ids = noderpc.env['product.product'].search([
                ('id', 'not in', remote_products_set_ids),
                '|', ('active', '=', True), ('active', '=', False)
            ])
            # disable mail feature to speed-up migration
            context_no_mail = {
                'tracking_disable': True,
                'mail_notrack': True,
                'mail_create_nolog': True,
            }
            for remote_product_id in remote_product_ids:
                try:
                    migrated_product = self.env['product.product'].search([
                        ('remote_id', '=', remote_product_id)
                    ]) or None

                    if not migrated_product:
                        rpc_product = noderpc.env['product.product'].browse(remote_product_id)

                        vals = {
                            'remote_id': remote_product_id,
                            'name': rpc_product.name,
                            'taxes_id': [[6, False, [rpc_product.taxes_id.id or 59]]], # vat 10% (services) as default
                            'list_price': rpc_product.list_price,
                            'type': 'service',
                            'sale_ok': True,
                            'purchase_ok': False,
                            'active': True,
                        }
                        migrated_product = self.env['product.product'].with_context(
                            context_no_mail
                        ).create(vals)
                        #
                        _logger.info('User #%s migrated product.product with ID [local, remote]: [%s, %s]',
                                     self._uid, migrated_product.id, remote_product_id)

                except (ValueError, ValidationError, Exception) as err:
                    migrated_log = self.env['migrated.log'].create({
                        'name': err,
                        'date_time': fields.Datetime.now(),
                        'migrated_hotel_id': self.id,
                        'model': 'product',
                        'remote_id': remote_product_id,
                    })
                    _logger.error('Remote product.product with ID remote: [%s] with ERROR LOG #%s: (%s)',
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
    def _prepare_folio_remote_data(self, rpc_hotel_folio,
                                   res_users_map_ids, category_map_ids):
        # prepare partner_id related field
        default_res_partner = self.env['res.partner'].search([
            ('user_ids', 'in', self._context.get('uid', self._uid))
        ])
        # search res_partner id
        remote_id = rpc_hotel_folio['partner_id'] and rpc_hotel_folio['partner_id'][0]
        res_partner_id = self.env['res.partner'].search([
            ('remote_id', '=', remote_id)
        ]).id or None
        # take into account merged partners are not active
        if not res_partner_id:
            res_partner_id = self.env['res.partner'].search([
                ('remote_id', '=', remote_id),
                ('active', '=', False)
            ]).main_partner_id.id or None
        res_partner_id = res_partner_id or default_res_partner.id

        # search res_partner invoice id
        remote_id = rpc_hotel_folio['partner_invoice_id'] and rpc_hotel_folio['partner_invoice_id'][0]
        res_partner_invoice_id = self.env['res.partner'].search([
            ('remote_id', '=', remote_id)
        ]).id or None
        # take into account merged partners are not active
        if not res_partner_invoice_id:
            res_partner_invoice_id = self.env['res.partner'].search([
                ('remote_id', '=', remote_id),
                ('active', '=', False)
            ]).main_partner_id.id or None
        res_partner_invoice_id = res_partner_invoice_id or default_res_partner.company_id.id

        # search res_users ids
        remote_id = rpc_hotel_folio['user_id'] and rpc_hotel_folio['user_id'][0]
        res_user_id = remote_id and res_users_map_ids.get(remote_id)
        remote_id = rpc_hotel_folio['create_uid'] and rpc_hotel_folio['create_uid'][0]
        res_create_uid = remote_id and res_users_map_ids.get(remote_id)

        # prepare category_ids related field
        remote_ids = rpc_hotel_folio['segmentation_id'] and rpc_hotel_folio['segmentation_id']
        category_ids = remote_ids and [category_map_ids.get(r) for r in remote_ids] or None

        # prepare default state value
        state = 'confirm'
        if rpc_hotel_folio['state'] != 'sale':
            state = rpc_hotel_folio['state']

        vals = {
            'remote_id': rpc_hotel_folio['id'],
            'name': rpc_hotel_folio['name'],
            'partner_id': res_partner_id,
            'partner_invoice_id': res_partner_invoice_id,
            'segmentation_ids': category_ids and [[6, False, category_ids]] or None,
            'reservation_type': rpc_hotel_folio['reservation_type'],
            'channel_type': rpc_hotel_folio['channel_type'],
            'customer_notes': rpc_hotel_folio['wcustomer_notes'],
            'internal_comment': rpc_hotel_folio['internal_comment'],
            'state': state,
            'cancelled_reason': rpc_hotel_folio['cancelled_reason'],
            'date_order': rpc_hotel_folio['date_order'],
            'confirmation_date': rpc_hotel_folio['confirmation_date'],
            'create_date': rpc_hotel_folio['create_date'],
            'user_id': res_user_id,
            'create_uid': res_create_uid,
            '__last_update': rpc_hotel_folio['__last_update'],
        }
        if rpc_hotel_folio['reservation_type'] == 'out':
            vals.update({'closure_reason_id': self.dummy_closure_reason_id.id})

        return vals

    @api.multi
    def _prepare_reservation_remote_data(self, folio_id, reservation,
                                   room_type_map_ids, room_map_ids, noderpc):

        remote_ids = reservation['reservation_lines'] and reservation['reservation_lines']
        hotel_reservation_lines = noderpc.env['hotel.reservation.line'].search_read(
            [('id', 'in', remote_ids)],
            ['date', 'price']
        )
        reservation_line_cmds = []
        for reservation_line in hotel_reservation_lines:
            reservation_line_cmds.append((0, False, {
                'date': reservation_line['date'],
                'price': reservation_line['price'],
                'discount': reservation['discount'],

            }))
        # prepare hotel_room_type related field
        remote_id = reservation['virtual_room_id'] and reservation['virtual_room_id'][0]
        room_type_id = remote_id and room_type_map_ids.get(remote_id) or None
        # prepare hotel_room related field
        remote_id = reservation['product_id'] and reservation['product_id'][0]
        room_id = remote_id and room_map_ids.get(remote_id) or None
        # prepare hotel.folio.room_lines
        vals = {
            'folio_id': folio_id,
            'remote_id': reservation['id'],
            'name': reservation['name'],
            'room_type_id': room_type_id,
            'room_id': room_id,
            'checkin': fields.Date.from_string(
                reservation['checkin']).strftime(DEFAULT_SERVER_DATE_FORMAT),
            'checkout': fields.Date.from_string(
                reservation['checkout']).strftime(DEFAULT_SERVER_DATE_FORMAT),
            'arrival_hour': fields.Datetime.from_string(
                reservation['checkin']).strftime('%H:%M'),
            'departure_hour': fields.Datetime.from_string(
                reservation['checkout']).strftime('%H:%M'),
            'nights': reservation['nights'],
            'to_assign': reservation['to_assign'],
            'to_send': reservation['to_send'],
            'state': reservation['state'],
            'cancelled_reason': reservation['cancelled_reason'],
            'out_service_description': reservation['out_service_description'],
            'adults': reservation['adults'],
            'children': reservation['children'],
            'splitted': reservation['splitted'],
            # 'parent_reservation': not yet implemented,
            'overbooking': reservation['overbooking'],
            'channel_type': reservation['channel_type'],
            'call_center': reservation['call_center'],
            'last_updated_res': reservation['last_updated_res'],
            'reservation_line_ids': reservation_line_cmds,
        }
        if reservation['channel_type'] == 'web':
            wubook_vals = {
                'backend_id': self.dummy_backend_id.id,
                'external_id': reservation['wrid'],
                'channel_raw_data': reservation['wbook_json'],
                'ota_id': reservation['wchannel_id'] and reservation['wchannel_id'][0] or None,
                'ota_reservation_id': reservation['wchannel_reservation_code'],
                'channel_status': reservation['wstatus'],
                'channel_status_reason': reservation['wstatus_reason'],
                'channel_modified': reservation['wmodified'],
            }
            vals.update({'channel_bind_ids': [(0, False, wubook_vals)]})

        return vals

    @api.multi
    def _prepare_folio_service_remote_data(self, hotel_folio_services):

        service_line_cmds = []
        for service in hotel_folio_services:
            # 'direct sale' reservations after D-date are migrated with no products
            if service['channel_type'] != 'web' and fields.Date.from_string(
                    service['ser_checkin']) >= fields.Date.from_string(self.migration_date_d):
                continue

            ser_room_line = service['ser_room_line'] and service['ser_room_line'][0] or None
            if ser_room_line:
                ser_room_line = self.env['hotel.reservation'].search([
                    ('remote_id', '=', ser_room_line)
                ]).id or None

            # reservations before D-date are migrated with Odoo 10 products
            service_line_cmds.append((0, False, {
                'product_id': self.env['product.product'].search([
                    ('remote_id', '=', service['product_id'][0])
                ]).id or None,
                'ser_room_line': ser_room_line,
                'name': service['name'],
                'product_qty': service['product_uom_qty'],
                'price_unit': service['price_unit'],
                'discount': service['discount'],
                'channel_type': service['channel_type'] or 'door',
            }))

        return service_line_cmds

    @api.multi
    def _prepare_folio_payment_remote_data(self, folio, account_payment, journal_map_ids):
        # search res_partner id
        remote_id = account_payment['partner_id'] and account_payment['partner_id'][0]
        res_partner_id = self.env['res.partner'].search([
            ('remote_id', '=', remote_id)
        ]).id or None
        # take into account merged partners are not active
        if not res_partner_id:
            res_partner_id = self.env['res.partner'].search([
                ('remote_id', '=', remote_id),
                ('active', '=', False)
            ]).main_partner_id.id or None
        if not res_partner_id:
            res_partner_id = folio.partner_id.id

        # prepare payment related field
        remote_id = account_payment['journal_id'] and account_payment['journal_id'][0]
        journal_id = remote_id and journal_map_ids.get(remote_id) or None

        # prepare payment vals
        return {
            'remote_id': account_payment['id'],
            'journal_id': journal_id,
            'partner_id': res_partner_id,
            'amount': account_payment['amount'],
            'payment_date': account_payment['payment_date'],
            'communication': account_payment['communication'],
            'folio_id': folio.id,
            'payment_type': 'inbound',
            'payment_method_id': 1,
            'partner_type': 'customer',
            'state': 'draft'
        }

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
                ]).id or self._context.get('uid', self._uid)
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

            # prepare account.journal ids
            _logger.info("Mapping local with remote 'account.journal' ids...")
            remote_ids = noderpc.env['account.journal'].search([])
            remote_records = noderpc.env['account.journal'].browse(remote_ids)
            journal_map_ids = {}
            for record in remote_records:
                res_journal_id = self.env['account.journal'].search([
                    ('name', '=', record.name),
                ]).id
                journal_map_ids.update({record.id: res_journal_id})

            # prepare reservation of interest
            _logger.info("Preparing 'hotel.folio' of interest...")
            remote_hotel_folio_ids = noderpc.env['hotel.folio'].search([])
            _logger.info("Migrating 'hotel.folio'...")
            # disable mail feature to speed-up migration
            context_no_mail = {
                'tracking_disable': True,
                'mail_notrack': True,
                'mail_create_nolog': True,
                'connector_no_export': True,
            }
            for remote_hotel_folio_id in remote_hotel_folio_ids:
                try:
                    _logger.info('User #%s started migration of hotel.folio with remote ID: [%s]',
                                 self._uid, remote_hotel_folio_id)

                    migrated_hotel_folio = self.env['hotel.folio'].search([
                        ('remote_id', '=', remote_hotel_folio_id)
                    ]) or None

                    if not migrated_hotel_folio:
                        rpc_hotel_folio = noderpc.env['hotel.folio'].search_read(
                            [('id', '=', remote_hotel_folio_id)],
                        )[0]

                        vals = self._prepare_folio_remote_data(
                            rpc_hotel_folio,
                            res_users_map_ids,
                            category_map_ids)
                        migrated_hotel_folio = self.env['hotel.folio'].with_context(
                            context_no_mail
                        ).create(vals)

                        # prepare room_lines related field
                        remote_ids = rpc_hotel_folio['room_lines'] and rpc_hotel_folio['room_lines']
                        hotel_reservations = noderpc.env['hotel.reservation'].search_read(
                            [('id', 'in', remote_ids)],
                            ['name',
                             'virtual_room_id',
                             'product_id',
                             'discount',
                             'checkin',
                             'checkout',
                             'nights',
                             'to_assign',
                             'to_send',
                             'state',
                             'cancelled_reason',
                             'out_service_description',
                             'adults',
                             'children',
                             'splitted',
                             # 'parent_reservation': not yet implemented,
                             'overbooking',
                             'channel_type',
                             'call_center',
                             'wrid',
                             'wbook_json',
                             'wchannel_id',
                             'wchannel_reservation_code',
                             'wstatus',
                             'wstatus_reason',
                             'wmodified',
                             'last_updated_res',
                             'reservation_lines',
                             ],
                            order='id ASC',  # assume splitted parents reservation has always lesser id
                        )
                        for reservation in hotel_reservations:
                            vals = self._prepare_reservation_remote_data(
                                migrated_hotel_folio.id,
                                reservation,
                                room_type_map_ids,
                                room_map_ids,
                                noderpc)
                            migrated_hotel_reservation = self.env['hotel.reservation'].with_context(
                                context_no_mail
                            ).create(vals)
                        # TODO: update parent_reservation_id for splitted reservation

                        # prepare service_lines related field
                        remote_ids = rpc_hotel_folio['service_lines'] and rpc_hotel_folio['service_lines']
                        hotel_folio_services = noderpc.env['hotel.service.line'].search_read(
                            [('id', 'in', remote_ids)],
                            ['name',
                             'product_id',
                             'product_uom_qty',
                             'price_unit',
                             'discount',
                             'channel_type',
                             'ser_room_line',
                             'ser_checkin',
                             'service_line_id',
                             ]
                        )
                        service_line_cmds = self._prepare_folio_service_remote_data(hotel_folio_services)
                        # all services are create in the folio at once
                        migrated_hotel_folio.with_context(
                            context_no_mail
                        ).write({'service_ids': service_line_cmds})

                        # prepare payment related field
                        remote_ids = rpc_hotel_folio['payment_ids'] and rpc_hotel_folio['payment_ids']
                        hotel_folio_payments = noderpc.env['account.payment'].search_read(
                            [('id', 'in', remote_ids)]
                        )
                        for account_payment in hotel_folio_payments:
                            vals = self._prepare_folio_payment_remote_data(
                                migrated_hotel_folio,
                                account_payment,
                                journal_map_ids)
                            migrated_hotel_payment = self.env['account.payment'].with_context(
                                context_no_mail
                            ).create(vals)
                            migrated_hotel_payment.with_context(
                                {'ignore_notification_post': True}
                            ).post()

                    _logger.info('User #%s migrated hotel.folio with ID [local, remote]: [%s, %s]',
                                 self._uid, migrated_hotel_folio.id, remote_hotel_folio_id)

                except (ValueError, ValidationError, Exception) as err:
                    migrated_log = self.env['migrated.log'].create({
                        'name': err,
                        'date_time': fields.Datetime.now(),
                        'migrated_hotel_id': self.id,
                        'model': 'folio',
                        'remote_id': remote_hotel_folio_id,
                    })
                    _logger.error('Remote hotel.folio with ID remote: [%s] with ERROR LOG #%s: (%s)',
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
    def action_migrate_payment_return(self):
        start_time = time.time()
        self.ensure_one()
        try:
            noderpc = odoorpc.ODOO(self.odoo_host, self.odoo_protocol, self.odoo_port)
            noderpc.login(self.odoo_db, self.odoo_user, self.odoo_password)
        except (odoorpc.error.RPCError, odoorpc.error.InternalError, urllib.error.URLError) as err:
            raise ValidationError(err)

        try:
            _logger.info("Preparing 'payment.return' of interest...")
            remote_payment_return_ids = noderpc.env['payment.return'].search(
                [('state', '=', 'done')]
            )
            _logger.info("Migrating 'payment.return'...")
            # disable mail feature to speed-up migration
            context_no_mail = {
                'tracking_disable': True,
                'mail_notrack': True,
                'mail_create_nolog': True,
            }
            for payment_return_id in remote_payment_return_ids:
                try:
                    payment_return_line = noderpc.env['payment.return'].browse(payment_return_id).line_ids

                    # prepare related payment
                    remote_payment_id = payment_return_line.move_line_ids.payment_id.id
                    account_payment = self.env['account.payment'].search([
                        ('remote_id','=', remote_payment_id)
                    ]) or None
                    account_move_lines = account_payment.move_line_ids.filtered(
                        lambda x: (x.account_id.internal_type == 'receivable')
                    )
                    line_ids_vals = {
                        'move_line_ids': [(6, False, [x.id for x in account_move_lines])],
                        'partner_id': account_payment.partner_id.id,
                        'amount': payment_return_line.amount,
                        'reference': payment_return_line.reference,
                    }
                    vals = {
                        'journal_id': account_payment.journal_id.id,
                        'line_ids': [(0, 0, line_ids_vals)],
                    }
                    payment_return = self.env['payment.return'].with_context(
                        context_no_mail
                    ).create(vals)
                    payment_return.action_confirm()

                    _logger.info('User #%s migrated payment.return for account.payment with ID [local, remote]: [%s, %s]',
                                 self._uid, account_payment.id, remote_payment_id)

                except (ValueError, ValidationError, Exception) as err:
                    migrated_log = self.env['migrated.log'].create({
                        'name': err,
                        'date_time': fields.Datetime.now(),
                        'migrated_hotel_id': self.id,
                        'model': 'return',
                        'remote_id': payment_return_id,
                    })
                    _logger.error('Remote payment.return with ID remote: [%s] with ERROR LOG #%s: (%s)',
                                  payment_return_id, migrated_log.id, err)
                    continue

            time_migration_products = (time.time() - start_time) / 60
            _logger.info('action_migrate_invoice elapsed time: %s minutes',
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

    @api.model
    def cron_migrate_res_partners(self):
        hotel = self.env[self._name].search([
            ('cron_ready', '=', True)
        ])
        hotel.action_migrate_res_partners()
        hotel.action_clean_up()

    @api.model
    def cron_migrate_reservations(self):
        hotel = self.env[self._name].search([
            ('cron_ready', '=', True)
        ])
        hotel.action_migrate_reservation()
        hotel.action_clean_up()

    @api.model
    def cron_migrate_hotel(self):
        self.cron_migrate_res_partners()
        self.cron_migrate_reservation()
