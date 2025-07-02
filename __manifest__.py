{
    'name': 'gia biometric attendance',
    'category': 'Human Resources',
    'description': "gian biometric attendance",
    'author': 'GIA',
    'depends': ['base_setup', 'hr_attendance'],
    'data': [
        'security/ir.model.access.csv',
        'report/paperformat_custom.xml',
        'report/header_template.xml',
        'report/biometric_attendance_report_templates.xml',
        'report/biometric_attendance_report.xml',
        'views/biometric_device_details_views.xml',
        'views/hr_employee_views.xml',
        'views/gia_attendance_views.xml',
        'views/biometric_device_attendance_menus.xml',
        'data/download_data.xml',


    ],
    'images': ['static/description/banner.png'],
    'license': 'AGPL-3',
    'installable': True,
    'auto_install': False,
    'application': False,
}
