from erpnext.manufacturing.doctype.work_order.work_order_dashboard import (
    get_data as get_core_data,
)


def get_data(*args, **kwargs):
    data = get_core_data()
    data.setdefault("non_standard_fieldnames", {})["Sub Pick List"] = "work_order"
    data["non_standard_fieldnames"]["Timesheet"] = "custom_work_order"

    transactions = data.setdefault("transactions", [])
    if transactions:
        items = transactions[0].setdefault("items", [])
        if "Sub Pick List" not in items:
            items.append("Sub Pick List")
        if "Timesheet" not in items:
            items.append("Timesheet")
    else:
        transactions.append(
            {"label": "Manufacturing", "items": ["Sub Pick List", "Timesheet"]}
        )
    return data
