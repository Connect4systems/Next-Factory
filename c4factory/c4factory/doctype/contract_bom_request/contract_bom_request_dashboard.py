from frappe import _


def get_data():
	return {
		"internal_links": {
			"BOM": ["items", "bom"],
		},
		"transactions": [
			{
				"label": _("Manufacturing"),
				"items": ["BOM"],
			},
		],
	}
