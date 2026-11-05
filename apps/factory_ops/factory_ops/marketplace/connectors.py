"""Marketplace connector adapters (MINTREE_ECOMMERCE_MODULE.md).

One adapter per provider; they do not share an API shape, so none is assumed. **No adapter makes a
live call today.** Live calls are written only after the provider's current official
documentation has been checked against a real seller account with real credentials. Until then:

- no credentials                 -> "Not configured"
- some but not all credentials   -> "Awaiting credentials"
- partner programme not granted  -> "Access required"
- all credentials, no live code  -> "Not implemented" (never "Connected")

"Connected" can only be set by a successful live `test_connection`, which no adapter performs yet.
Secrets are read on the server with `get_password` and never leave it.
"""

from __future__ import annotations

import frappe


class ConnectorError(Exception):
	"""A failure the sync log records; `retryable` failures are retried with back-off."""

	retryable = False


class RateLimited(ConnectorError):
	retryable = True

	def __init__(self, message: str, retry_after: float = 60):
		super().__init__(message)
		self.retry_after = retry_after


class NotConnected(ConnectorError):
	"""The channel cannot be called: credentials, access or live implementation missing."""


class Connector:
	provider = ""
	#: credential fields this provider needs (Marketplace Channel fieldnames)
	required: tuple[str, ...] = ()
	#: partner programmes that must be granted before any API use
	access_required = False
	access_note = ""

	def __init__(self, channel):
		self.channel = channel

	def has(self, field: str) -> bool:
		meta = frappe.get_meta("Marketplace Channel").get_field(field)
		if meta and meta.fieldtype == "Password":
			return bool(self.channel.get_password(field, raise_exception=False))
		return bool(self.channel.get(field))

	def state(self) -> str:
		if self.access_required and not self.channel.get("access_granted"):
			return "Access required"
		present = [f for f in self.required if self.has(f)]
		if not present:
			return "Not configured"
		if len(present) < len(self.required):
			return "Awaiting credentials"
		return "Not implemented"

	def missing(self) -> list[str]:
		return [f for f in self.required if not self.has(f)]

	def _refuse(self, operation: str):
		state = self.state()
		raise NotConnected(
			f"{self.channel.name}: {operation} not sent. State: {state}. "
			+ (f"Missing: {', '.join(self.missing())}. " if state == "Awaiting credentials" else "")
			+ "Live calls are written only after the official API documentation is verified with a real seller account."
		)

	# The operations every adapter exposes. None is implemented live yet.
	def test_connection(self) -> str:
		self._refuse("connection test")

	def push_inventory(self, rows: list[dict]) -> int:
		self._refuse("inventory update")

	def pull_orders(self, since=None) -> list[dict]:
		self._refuse("order download")


class AmazonSPAPI(Connector):
	provider = "Amazon SP-API"
	# Login with Amazon (LWA) app credentials and the seller's refresh token; marketplace ID per region.
	required = ("seller_id", "marketplace_region", "client_id", "client_secret", "refresh_token")
	access_note = "Needs a registered SP-API developer application authorised by the seller (LWA client ID / secret, refresh token) and the marketplace ID."


class FlipkartSellerAPI(Connector):
	provider = "Flipkart Seller API"
	required = ("seller_id", "client_id", "client_secret")
	access_note = "Needs a Flipkart Seller API application (application ID and secret) approved for the seller account."


class MyntraPartnerAPI(Connector):
	provider = "Myntra Partner API"
	required = ("seller_id", "client_id", "client_secret")
	access_required = True
	access_note = "Myntra's partner APIs are available only to onboarded partners; access must be requested from Myntra. To be confirmed with the channel."


class NykaaSellerAPI(Connector):
	provider = "Nykaa Seller API"
	required = ("seller_id", "client_id", "client_secret")
	access_required = True
	access_note = "Nykaa does not publish an open seller API; integration access must be requested from Nykaa. To be confirmed with the channel."


ADAPTERS = {c.provider: c for c in (AmazonSPAPI, FlipkartSellerAPI, MyntraPartnerAPI, NykaaSellerAPI)}


def connector_for(channel) -> Connector:
	cls = ADAPTERS.get(channel.provider)
	if not cls:
		raise ConnectorError(f"No connector for provider {channel.provider}")
	return cls(channel)
