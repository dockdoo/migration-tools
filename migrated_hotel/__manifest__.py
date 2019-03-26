{
    'name': 'Hotel Migration Tool',
    'summary': """Provides a custom migration from hootel 10.0 to hootel 11.0""",
    'version': '0.1.0',
    'author': 'Pablo Q. Barriuso, \
               Darío Lodeiros',
    'category': 'Generic Modules/Hotel Management',
    'depends': [
        'hotel',
    ],
    'external_dependencies':
        {'python' : ['odoorpc']},
    'license': "AGPL-3",
    'data': [
        'views/migrated_hotel_views.xml',
        'views/inherited_res_partner_views.xml',
        'security/migrated_hotel_security.xml',
        'security/ir.model.access.csv',
    ],
    'demo': [],
    'auto_install': False,
    'installable': True
}
