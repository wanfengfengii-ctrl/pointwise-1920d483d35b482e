import sqlite3

from archive.database import BeforeCommit, archive_write
from archive.events import load_event_values, merge_event_values
from archive.integrity import capture_seal, retest_seal
from archive.originals import store_original
from archive.readings import read_capture_evidence
from archive.receipts import (
    NAMED_KIND,
    RETEST_KIND,
    ReceiptRecord,
    find_receipts_by_import,
    find_retests_by_import,
    insert_receipt,
    new_receipt_id,
)
from archive.records import FixedReading, insert_readings
from intake.identity import NamedImportIdentity, RetestImportIdentity


def commit_capture(
    db: sqlite3.Connection,
    identity: NamedImportIdentity,
    version: str,
    readings: tuple[FixedReading, ...],
    original: bytes,
    *,
    before_commit: BeforeCommit | None = None,
) -> str:
    with archive_write(db, before_commit=before_commit):
        for candidate in find_receipts_by_import(db, identity):
            _, archived_original = read_capture_evidence(db, candidate.receipt)
            if archived_original == original:
                return candidate.receipt

        receipt = new_receipt_id()
        record = ReceiptRecord(
            receipt=receipt,
            registration_id=identity.registration_id,
            version=version,
            original_digest=identity.original_digest,
            seal=capture_seal(receipt, identity.registration_id, version, readings, original),
            kind=NAMED_KIND,
            event_id=None,
        )
        insert_receipt(db, record)
        insert_readings(db, receipt, readings)
        store_original(db, receipt, original)
    return receipt


def commit_retest(
    db: sqlite3.Connection,
    identity: RetestImportIdentity,
    version: str,
    readings: tuple[FixedReading, ...],
    original: bytes,
    *,
    before_commit: BeforeCommit | None = None,
) -> str:
    with archive_write(db, before_commit=before_commit):
        for candidate in find_retests_by_import(db, identity):
            _, archived_original = read_capture_evidence(db, candidate.receipt)
            if archived_original == original:
                load_event_values(db, identity.registration_id, identity.event_id)
                return candidate.receipt

        merge_event_values(db, identity.registration_id, identity.event_id, readings)
        receipt = new_receipt_id()
        record = ReceiptRecord(
            receipt=receipt,
            registration_id=identity.registration_id,
            version=version,
            original_digest=identity.original_digest,
            seal=retest_seal(
                receipt,
                identity.registration_id,
                version,
                identity.event_id,
                readings,
                original,
            ),
            kind=RETEST_KIND,
            event_id=identity.event_id,
        )
        insert_receipt(db, record)
        insert_readings(db, receipt, readings)
        store_original(db, receipt, original)
    return receipt
