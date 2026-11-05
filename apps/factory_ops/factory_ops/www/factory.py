"""Serves the built Factory OS front end (apps/web) at /factory and every path below it.

`pnpm build` in apps/web writes www/factory.html and public/factory/; the page signs in through
Frappe's own login and then uses the session cookie, so it must be on this same origin.
"""

no_cache = 1


def get_context(context):
	context.no_cache = 1
