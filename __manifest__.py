{
    "name": "Partner Multi Order Return",
    "summary": "Process customer and vendor returns across historical orders and invoices",
    "version": "18.0.1.0.10",
    "category": "Inventory/Inventory",
    "author": "Custom Development",
    "license": "LGPL-3",
    "website": "",
    "depends": ["sale", "purchase", "stock", "account"],
    "data": [
        "security/ir.model.access.csv",
        "security/return_order_security.xml",
        "views/return_order_views.xml",
        "views/purchase_return_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "application": True,
    "installable": True,
}
