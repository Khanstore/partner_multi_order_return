{
    "name": "Partner Multi Order Return",
    "summary": "Process customer returns across historical sales and invoices",
    "version": "18.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Custom Development",
    "license": "LGPL-3",
    "website": "",
    "depends": ["sale", "stock", "account"],
    "data": [
        "security/ir.model.access.csv",
        "views/return_order_views.xml",
    ],
    "application": True,
    "installable": True,
}
