"""SQLite storage: connection management, migrations, transactions.

One file is the single source of truth for everything HEDWIG knows (docs/05 §2). This
package owns the connection to it; every other module goes through a repository or a
service, never through raw SQL of its own.
"""

from __future__ import annotations

from hedwig.core.store.database import Database, Transaction
from hedwig.core.store.migrations import MigrationError, MigrationRunner, applied_versions

__all__ = [
    "Database",
    "MigrationError",
    "MigrationRunner",
    "Transaction",
    "applied_versions",
]
