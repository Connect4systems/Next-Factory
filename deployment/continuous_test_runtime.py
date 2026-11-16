"""Isolated staging runtime when production shares the existing Bench.

Run with this repository first on PYTHONPATH. Never replace shared app files.
The web entrypoint refuses every site except the explicitly named staging site.
The private worker queue must also be configured on that staging site alone.
"""

import os
import sys
import time

import frappe

SITE = "napata.connect4systems.com"
QUEUE = "continuous-cost-test"
original_get_hooks = frappe.get_hooks


def isolated_get_hooks(hook=None, default="_KEEP_DEFAULT_LIST", app_name=None):
	# Shared Redis must never receive the isolated app's hook map. Otherwise a
	# shared worker could try to import modules absent from its original checkout.
	if getattr(frappe.local, "site", None) != SITE or app_name:
		return original_get_hooks(hook, default, app_name)
	hooks = frappe._dict(frappe._load_app_hooks())
	if hook:
		return hooks.get(hook, [] if default == "_KEEP_DEFAULT_LIST" else default)
	return hooks


frappe.get_hooks = isolated_get_hooks

from frappe.utils import background_jobs

original_queue_timeouts = background_jobs.get_queues_timeout


def isolated_queue_timeouts():
	return {**original_queue_timeouts(), QUEUE: 1800}


# Register the staging queue in these processes only, never in the shared Bench
# configuration used by production workers.
background_jobs.get_queues_timeout = isolated_queue_timeouts

from frappe.app import application as frappe_application


def application(environ, start_response):
	site = environ.get("HTTP_X_FRAPPE_SITE_NAME") or environ.get("HTTP_HOST", "").split(":")[0]
	if site != SITE:
		start_response("403 Forbidden", [("Content-Type", "text/plain")])
		return [b"This isolated application only serves the authorized staging site."]
	return frappe_application(environ, start_response)


def sync_status():
	from c4factory.api.work_order_costing import sync_pending_settlements

	while True:
		try:
			frappe.init(site=SITE, sites_path=os.getcwd())
			frappe.connect()
			frappe.set_user("Administrator")
			sync_pending_settlements()
			frappe.db.commit()
		except Exception:
			if getattr(frappe.local, "db", None):
				frappe.db.rollback()
			import traceback

			traceback.print_exc()
		finally:
			frappe.destroy()
		time.sleep(15)


if __name__ == "__main__":
	if sys.argv[1:] == ["worker"]:
		from frappe.utils.background_jobs import start_worker

		start_worker(queue=QUEUE)
	elif sys.argv[1:] == ["sync"]:
		sync_status()
	else:
		raise SystemExit("Use gunicorn for web, or pass worker / sync")
