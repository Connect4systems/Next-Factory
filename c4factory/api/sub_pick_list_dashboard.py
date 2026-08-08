from frappe import _


def get_data(*args, **kwargs):
    return {
        "fieldname": "custom_sub_pick_list",
        "transactions": [
            {
                "label": _("Stock"),
                "items": ["Stock Entry"],
            }
        ],
    }
