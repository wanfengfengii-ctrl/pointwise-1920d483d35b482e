import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from intake.identity import NamedImportIdentity, RetestImportIdentity

NAMED_KIND = 'named'
RETEST_KIND = 'retest'
RECEIPT_KINDS = (NAMED_KIND, RETEST_KIND)


@dataclass(frozen=True, slots=True)
class ReceiptRecord:
    receipt: str
    registration_id: str
    version: str
    original_digest: str
    seal: str
    kind: str
    event_id: str | None

    @property
    def is_retest(self) -> bool:
        return self.kind == RETEST_KIND


_FIELDS = 'receipt, registration_id, version, original_digest, seal, kind, event_id'


def new_receipt_id() -> str:
    return f'cap-{uuid4().hex}'


def _column_names(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f'PRAGMA table_info({table})')}


def _upgrade_receipt_table(db: sqlite3.Connection) -> None:
    columns = _column_names(db, 'capture_receipts')
    if 'kind' not in columns:
        db.execute(
            "ALTER TABLE capture_receipts ADD COLUMN kind TEXT NOT NULL DEFAULT 'named'"
        )
    if 'event_id' not in columns:
        db.execute('ALTER TABLE capture_receipts ADD COLUMN event_id TEXT')


def init_receipt_store(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS capture_receipts (
            receipt TEXT PRIMARY KEY NOT NULL,
            registration_id TEXT NOT NULL REFERENCES rosters (registration_id),
            version TEXT NOT NULL,
            original_digest TEXT NOT NULL,
            seal TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'named' CHECK (kind IN ('named', 'retest')),
            event_id TEXT
        )
    ''')
    _upgrade_receipt_table(db)
    db.execute('''
        CREATE INDEX IF NOT EXISTS capture_import_candidates
        ON capture_receipts (registration_id, original_digest)
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS retest_event_candidates
        ON capture_receipts (registration_id, event_id)
    ''')


def insert_receipt(db: sqlite3.Connection, record: ReceiptRecord) -> None:
    db.execute(
        f'INSERT INTO capture_receipts ({_FIELDS}) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (
            record.receipt,
            record.registration_id,
            record.version,
            record.original_digest,
            record.seal,
            record.kind,
            record.event_id,
        ),
    )


def load_receipt(db: sqlite3.Connection, receipt: str) -> ReceiptRecord | None:
    row = db.execute(
        f'SELECT {_FIELDS} FROM capture_receipts WHERE receipt = ?', (receipt,)
    ).fetchone()
    return None if row is None else ReceiptRecord(*row)


def find_receipts_by_import(
    db: sqlite3.Connection, identity: NamedImportIdentity
) -> tuple[ReceiptRecord, ...]:
    rows = db.execute(
        f'SELECT {_FIELDS} FROM capture_receipts '
        'WHERE registration_id = ? AND original_digest = ? AND kind = ? ORDER BY receipt',
        (identity.registration_id, identity.original_digest, NAMED_KIND),
    )
    return tuple(ReceiptRecord(*row) for row in rows)


def find_retests_by_import(
    db: sqlite3.Connection, identity: RetestImportIdentity
) -> tuple[ReceiptRecord, ...]:
    rows = db.execute(
        f'SELECT {_FIELDS} FROM capture_receipts '
        'WHERE registration_id = ? AND original_digest = ? AND kind = ? AND event_id = ? '
        'ORDER BY receipt',
        (
            identity.registration_id,
            identity.original_digest,
            RETEST_KIND,
            identity.event_id,
        ),
    )
    return tuple(ReceiptRecord(*row) for row in rows)


def find_receipts_by_event(
    db: sqlite3.Connection, registration_id: str, event_id: str
) -> tuple[ReceiptRecord, ...]:
    rows = db.execute(
        f'SELECT {_FIELDS} FROM capture_receipts '
        'WHERE registration_id = ? AND event_id = ? AND kind = ? ORDER BY receipt',
        (registration_id, event_id, RETEST_KIND),
    )
    return tuple(ReceiptRecord(*row) for row in rows)
