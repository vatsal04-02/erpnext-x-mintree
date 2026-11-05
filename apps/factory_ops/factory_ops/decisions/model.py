# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""The Decision Centre's data model: one Entry per pending decision or problem.

Entries are *derived*, never stored: each source reads the real ERPNext / factory_ops documents with
one permission-aware query, so a resolved issue disappears the moment its document changes and nothing
is duplicated into another table. An entry names the underlying document; it never replaces it.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime

CRITICAL, HIGH, MEDIUM, LOW = "Critical", "High", "Medium", "Low"
PRIORITIES = (CRITICAL, HIGH, MEDIUM, LOW)
PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}  # 0 = most urgent

# Departments (the order is the order of the queue's sections)
PROCUREMENT = "Procurement"
INVENTORY = "Inventory & Stores"
PRODUCTION = "Production"
QUALITY = "Quality Control"
DISPATCH = "Dispatch & Sales"
MAINTENANCE = "Plant Maintenance"
ACCOUNTS = "Accounts"
DEPARTMENTS = (PROCUREMENT, INVENTORY, PRODUCTION, QUALITY, DISPATCH, MAINTENANCE, ACCOUNTS)

# What the person looking at the queue can tell at a glance
PENDING, OVERDUE, BLOCKED, ATTENTION = "Pending", "Overdue", "Blocked", "Needs attention"
STATES = (PENDING, OVERDUE, BLOCKED, ATTENTION)


def bump(current: str, target: str) -> str:
	"""The more urgent of two priorities."""
	return current if PRIORITY_RANK[current] <= PRIORITY_RANK[target] else target


@dataclass
class Action:
	"""What the viewer may do about an entry. ``permitted`` is computed from Frappe permissions (and, for
	an approval, the approval rules); it is information for the button, the server checks again."""

	id: str  # approve_po, submit, open, ...
	label: str
	permitted: bool = True
	reason: str = ""  # why not, when not permitted


@dataclass
class Entry:
	kind: str  # registry id, e.g. "po_approval"
	category: str  # human label of the kind
	department: str  # primary department
	issue: str  # one line: what is wrong or waiting
	priority: str
	ref_doctype: str
	ref_name: str
	status: str = ""  # the underlying document's own status
	state: str = PENDING
	since: datetime | None = None  # when it became pending (creation, due date, failure date ...)
	responsible: str = ""  # a user id (assigned, else the document owner), "" if unknown
	raised_by: str = ""  # who created the underlying document
	responsible_department: str = ""  # who to chase when no person is named
	also: tuple = ()  # other departments that should see it (never a second row)
	amount: float | None = None
	overdue: bool = False
	blocker: bool = False  # stops other work (a production blocker)
	precedence: int = 50  # when two sources describe one document, the lower number wins
	action: Action | None = None
	key: str = field(default="")

	def __post_init__(self):
		self.key = f"{self.kind}:{self.ref_doctype}:{self.ref_name}"

	def to_dict(self) -> dict:
		d = asdict(self)
		d["since"] = self.since.isoformat(sep=" ", timespec="seconds") if self.since else None
		d["also"] = list(self.also)
		return d
