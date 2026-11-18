import json
from hashlib import sha256

from archive.receipts import NAMED_KIND, RETEST_KIND, ReceiptRecord
from archive.records import FixedReading
from intake.identity import identify_named_import


class EvidenceMismatch(RuntimeError):
    pass


def _seal_digest(snapshot: object) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return sha256(encoded).hexdigest()


def capture_seal(
    receipt: str,
    registration_id: str,
    version: str,
    readings: tuple[FixedReading, ...],
    original: bytes,
) -> str:
    source = identify_named_import(registration_id, original)
    snapshot = [
        'named-capture-v1',
        receipt,
        registration_id,
        version,
        source.original_digest,
        [[row.specimen_id, hex(row.value), *row.location.as_key()] for row in readings],
    ]
    return _seal_digest(snapshot)


def retest_seal(
    receipt: str,
    registration_id: str,
    version: str,
    event_id: str,
    readings: tuple[FixedReading, ...],
    original: bytes,
) -> str:
    source = identify_named_import(registration_id, original)
    snapshot = [
        'retest-capture-v1',
        receipt,
        registration_id,
        version,
        event_id,
        source.original_digest,
        [[row.specimen_id, hex(row.value), *row.location.as_key()] for row in readings],
    ]
    return _seal_digest(snapshot)


def verify_capture_seal(
    record: ReceiptRecord, readings: tuple[FixedReading, ...], original: bytes | None
) -> None:
    if original is None or not isinstance(original, bytes):
        raise EvidenceMismatch(f'original unavailable for {record.receipt}')
    identity = identify_named_import(record.registration_id, original)
    if identity.original_digest != record.original_digest:
        raise EvidenceMismatch(f'original digest differs for {record.receipt}')

    if record.kind == NAMED_KIND:
        if record.event_id is not None:
            raise EvidenceMismatch(f'unexpected event identity for named capture {record.receipt}')
        expected = capture_seal(
            record.receipt, record.registration_id, record.version, readings, original
        )
    elif record.kind == RETEST_KIND:
        if record.event_id is None:
            raise EvidenceMismatch(f'missing event identity for retest capture {record.receipt}')
        expected = retest_seal(
            record.receipt,
            record.registration_id,
            record.version,
            record.event_id,
            readings,
            original,
        )
    else:
        raise EvidenceMismatch(f'unknown capture kind {record.kind!r} for {record.receipt}')

    if expected != record.seal:
        raise EvidenceMismatch(f'frozen capture differs for {record.receipt}')
