"""Names shared across factory_ops. Changing a value here is a migration, not a refactor."""

# Roles created by factory_ops (ERPNext's own roles are reused for everything else).
ROLE_QC_OFFICER = "QC Officer"
ROLE_QA_MANAGER = "QA Manager"
QC_ROLES = (ROLE_QC_OFFICER, ROLE_QA_MANAGER)
ROLE_BUSINESS_ADMIN = "Business Administrator"  # the owner: see everything, approve within limits, configure nothing
FACTORY_ROLES = (*QC_ROLES, ROLE_BUSINESS_ADMIN)

# Warehouse Types. The warehouse a batch sits in is its QC status of record.
WH_TYPE_QC_HOLD = "QC Hold"
WH_TYPE_USABLE = "Usable"
WH_TYPE_REJECTED = "Rejected"
WH_TYPE_RETAINED = "Retained Samples"
WH_TYPE_JOB_WORK = "Job Work"
# Finished goods that are expired or below the minimum shelf life for dispatch (Milestone 5, D-028)
WH_TYPE_NOT_FOR_DISPATCH = "Not for Dispatch"
FACTORY_WAREHOUSE_TYPES = (
	WH_TYPE_QC_HOLD,
	WH_TYPE_USABLE,
	WH_TYPE_REJECTED,
	WH_TYPE_RETAINED,
	WH_TYPE_JOB_WORK,
	WH_TYPE_NOT_FOR_DISPATCH,
)

# QC Release decisions.
DECISION_RELEASE = "Release"
DECISION_PARTIAL = "Partial Release"
DECISION_CONDITIONAL = "Conditional Release"
DECISION_REJECT = "Reject"
RELEASE_DECISIONS = (DECISION_RELEASE, DECISION_PARTIAL, DECISION_CONDITIONAL)

# Derived batch QC status (Batch.fo_qc_status). Informational; the ledger is the truth.
BATCH_HOLD = "Hold"
BATCH_PARTLY_RELEASED = "Partly Released"
BATCH_RELEASED = "Released"
BATCH_REJECTED = "Rejected"

# Warehouse tree created for each company (name, parent, warehouse type, is_group).
# ERPNext appends " - <company abbr>" to every warehouse name.
WAREHOUSE_TREE = (
	("QUARANTINE", None, None, 1),
	("RM-QC-HOLD", "QUARANTINE", WH_TYPE_QC_HOLD, 0),
	("PM-QC-HOLD", "QUARANTINE", WH_TYPE_QC_HOLD, 0),
	("BULK-QC-HOLD", "QUARANTINE", WH_TYPE_QC_HOLD, 0),
	("FG-QC-HOLD", "QUARANTINE", WH_TYPE_QC_HOLD, 0),
	("RETURNS-QC-HOLD", "QUARANTINE", WH_TYPE_QC_HOLD, 0),
	("USABLE", None, None, 1),
	("RM-STORES", "USABLE", WH_TYPE_USABLE, 0),
	("PM-STORES", "USABLE", WH_TYPE_USABLE, 0),
	("BULK-STORES", "USABLE", WH_TYPE_USABLE, 0),
	("FG-STORES", "USABLE", WH_TYPE_USABLE, 0),
	("WIP-PRODUCTION", "USABLE", WH_TYPE_USABLE, 0),
	("REJECTED", None, WH_TYPE_REJECTED, 0),
	("RETAINED-SAMPLES", None, WH_TYPE_RETAINED, 0),
	("NOT-FOR-DISPATCH", None, WH_TYPE_NOT_FOR_DISPATCH, 0),
	("JOB-WORK", None, WH_TYPE_JOB_WORK, 1),
)

# Work-in-progress warehouse that Work Orders transfer components into.
WIP_WAREHOUSE = "WIP-PRODUCTION"

# Default release destination for each hold warehouse (Warehouse.fo_release_warehouse).
# RETURNS-QC-HOLD releases to FG-STORES (restock).
RELEASE_MAP = {
	"RM-QC-HOLD": "RM-STORES",
	"PM-QC-HOLD": "PM-STORES",
	"BULK-QC-HOLD": "BULK-STORES",
	"FG-QC-HOLD": "FG-STORES",
	"RETURNS-QC-HOLD": "FG-STORES",
}

# Sales channels on the Sales Order (Milestone 5). Marketplace orders are entered or imported by
# hand until the Phase 7 connectors exist.
SALES_CHANNEL_B2B = "B2B"
D2C_CHANNELS = ("D2C Website", "D2C Marketplace", "D2C Other")
SALES_CHANNELS = (SALES_CHANNEL_B2B, *D2C_CHANNELS)

# ERPNext Quality Inspection type per factory item group (QC configuration, Milestone 6)
INSPECTION_TYPE_BY_GROUP = {"RM": "Incoming", "PM": "Incoming", "BULK": "In Process", "FG": "Outgoing"}
