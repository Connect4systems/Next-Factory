// Copyright (c) 2025, Connect 4 Systems and contributors
// For license information, please see license.txt

frappe.ui.form.on("Contract BOM Item", {
	create_bom: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];

		if (frm.is_new()) {
			frappe.msgprint(__("Please save Contract BOM Request first, then create BOM."));
			return;
		}

		if (row.__islocal || !row.name || row.name.indexOf("new-") === 0) {
			frappe.msgprint(__("Please save the row first, then create BOM."));
			return;
		}

		if (!row.item) {
			frappe.msgprint(__("Please set an Item in the row before creating a BOM."));
			return;
		}

		if (row.bom) {
			frappe.confirm(
				__("A BOM already exists for this row ({0}). Create a new one anyway?", [row.bom]),
				function() { do_create(frm, cdt, cdn, row); }
			);
			return;
		}

		do_create(frm, cdt, cdn, row);
	}
});

function do_create(frm, cdt, cdn, row) {
	frappe.call({
		method: "c4factory.c4factory.doctype.contract_bom_request.contract_bom_request.create_bom_for_item",
		args: {
			item: row.item,
			qty: row.qty || 1,
			company: frm.doc.company || frappe.defaults.get_default("company"),
			contract_bom_request: frm.doc.name,
			contract_bom_item: row.name
		},
		freeze: true,
		freeze_message: __("Creating BOM..."),
		callback: function(r) {
			if (r.message) {
				frappe.model.set_value(cdt, cdn, "bom", r.message);
				frm.reload_doc().then(function() {
					if (frm.dashboard && frm.dashboard.set_open_count) {
						frm.dashboard.set_open_count();
					}
					frappe.show_alert({
						message: __("BOM {0} created and linked.", [r.message]),
						indicator: "green"
					});
				});
			}
		}
	});
}
