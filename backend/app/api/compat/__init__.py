"""Compatibility evidence shared across staged backend takeover phases."""

from backend.app.api.compat.data_fingerprint import (
    DataFingerprintError,
    capture_fingerprint,
    compare_fingerprints,
    fingerprint_database,
)
from backend.app.api.compat.legacy_reconciliation import (
    LegacyReconciliationError,
    assert_reconciliation_gate,
    capture_legacy_reconciliation,
    reconcile_legacy_database,
)

__all__ = [
    "DataFingerprintError",
    "LegacyReconciliationError",
    "assert_reconciliation_gate",
    "capture_fingerprint",
    "capture_legacy_reconciliation",
    "compare_fingerprints",
    "fingerprint_database",
    "reconcile_legacy_database",
]
