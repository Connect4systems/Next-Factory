# c4factory/hooks.py

app_name = "c4factory"
app_title = "C4Factory"
app_publisher = "Connect 4 Systems"
app_description = "Factory App"
app_email = "info@connect4systems.com"
app_license = "mit"

# ---------------------------------------------------------
# Client Scripts
# ---------------------------------------------------------

doctype_js = {
    "Pick List": "public/js/doctype/pick_list.js",
    "Stock Entry": "public/js/doctype/stock_entry.js",
    "BOM": "public/js/doctype/bom/bom_measurement_qty.js",
    "Work Order": "public/js/doctype/work_order.js",
    "Job Card": "public/js/doctype/job_card.js",

    # moved from c4napata → c4factory
    "Sales Order": "public/js/doctype/sales_order/request_bom.js",
    "Sample Request": "public/js/doctype/sample_request/sample_request.js",
}

# ---------------------------------------------------------
# Doc Events (server hooks)
# ---------------------------------------------------------

doc_events = {
    # Work Order – source warehouse autofill from Item Group
    "Work Order": {
        "validate": [
            "c4factory.c4_manufacturing.work_order_hooks.set_source_warehouse_from_item_group",
            "c4factory.c4_manufacturing.work_order_hooks.update_scrap_and_costing",
        ],
    },

    # Pick List custom flow
    "Pick List": {
        "before_validate": "c4factory.api.work_order_flow.on_pick_list_validate",
        "validate": "c4factory.api.work_order_flow.on_pick_list_validate",
        "on_submit": "c4factory.api.work_order_flow.on_pick_list_submit",
        "before_cancel": "c4factory.c4factory.doctype.sub_pick_list.sub_pick_list.prevent_main_pick_list_cancel",
        "on_cancel": "c4factory.api.work_order_flow.on_pick_list_cancel",
        "on_trash": "c4factory.api.work_order_flow.on_pick_list_trash",
    },

    # Stock Entry – costing + WO update
    "Stock Entry": {
        "validate": [
            "c4factory.c4_manufacturing.stock_entry_hooks.validate_additional_material_transfer",
            "c4factory.c4_manufacturing.stock_entry_hooks.set_wip_target_warehouse",
        ],
        "before_submit": [
            "c4factory.c4_manufacturing.stock_entry_hooks.validate_additional_material_transfer",
            "c4factory.c4_manufacturing.stock_entry_hooks.set_wip_target_warehouse",
            "c4factory.c4_manufacturing.stock_entry_hooks.set_pick_list_transferred_production_qty",
        ],
        "on_submit": [
            "c4factory.c4_manufacturing.stock_entry_hooks.apply_additional_material_to_work_order",
            "c4factory.c4_manufacturing.stock_entry_hooks.on_submit_update_work_order_costing",
            "c4factory.api.work_order_flow.on_stock_entry_submit",
            "c4factory.c4factory.doctype.sub_pick_list.sub_pick_list.update_from_stock_entry",
        ],
        "on_cancel": [
            "c4factory.c4_manufacturing.stock_entry_hooks.reverse_additional_material_from_work_order",
            "c4factory.api.work_order_flow.on_stock_entry_cancel",
            "c4factory.c4factory.doctype.sub_pick_list.sub_pick_list.update_from_stock_entry",
        ],
        "on_trash": "c4factory.api.work_order_flow.on_stock_entry_trash",
    },

    # Job Card – keep partial completion from becoming process loss
    "Job Card": {
        "before_validate": "c4factory.c4_manufacturing.job_card_hooks.set_operation_row_reference",
        "validate": "c4factory.c4_manufacturing.job_card_hooks.set_operation_row_reference",
        "before_submit": [
            "c4factory.c4_manufacturing.job_card_hooks.set_operation_row_reference",
            "c4factory.c4_manufacturing.job_card_hooks.normalize_partial_completion",
        ],
        "on_update": "c4factory.c4_manufacturing.job_card_hooks.sync_work_order_costing_from_job_card",
        "on_update_after_submit": "c4factory.c4_manufacturing.job_card_hooks.sync_work_order_costing_from_job_card",
        "on_submit": "c4factory.c4_manufacturing.job_card_hooks.sync_work_order_costing_from_job_card",
        "on_cancel": "c4factory.c4_manufacturing.job_card_hooks.sync_work_order_costing_from_job_card",
    },
}

# ---------------------------------------------------------
# Whitelisted method / class overrides
# ---------------------------------------------------------

# Do NOT override Work Order.make_stock_entry here.
# We keep the original finished-goods costing logic from c4pricing.
override_doctype_class = {
    "Work Order": "c4factory.overrides.work_order.WorkOrder",
    "Stock Entry": "c4factory.overrides.stock_entry.StockEntry",
}

# ---------------------------------------------------------
# Database patches for custom fields
# ---------------------------------------------------------

patches = [
    # Work Order scrap & costing fields
    "c4factory.patches.v1_0.setup_work_order_custom_fields",
    # WO / Pick List / Stock Entry extra fields (includes custom_pl_qty)
    "c4factory.patches.v1_0.setup_wo_pl_se_custom_fields",
    # Pick List real operation cost from linked Job Cards
    "c4factory.patches.v1_0.setup_pick_list_operation_cost_fields",
    # Allow users to close a partial Pick List and waive its remainder
    "c4factory.patches.v1_0.setup_manual_pick_list_completion",
    # Additional material transfers linked to a Pick List and Work Order
    "c4factory.patches.v1_0.setup_additional_material_flow",
    "c4factory.patches.v1_0.setup_sub_pick_list_stock_fields",
    "c4factory.patches.v1_0.setup_work_order_additional_material_table",
    "c4factory.patches.v1_0.setup_continuous_start_transfer",
    "c4factory.patches.v1_0.setup_continuous_start_pick_list_field",
    "c4factory.patches.v1_0.setup_continuous_start_qty_field",
]

override_doctype_dashboards = {
    "Pick List": "c4factory.api.pick_list_dashboard.get_data",
    "Sub Pick List": "c4factory.api.sub_pick_list_dashboard.get_data",
    "Work Order": "c4factory.api.work_order_dashboard.get_data",
}

# ---------------------------------------------------------
# Whitelisted method overrides
# ---------------------------------------------------------

override_whitelisted_methods = {
    "erpnext.manufacturing.doctype.work_order.work_order.create_pick_list": (
        "c4factory.api.work_order_pick_list.create_pick_list"
    ),
    "erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry": (
        "c4factory.api.work_order_stock.make_stock_entry"
    ),
}
