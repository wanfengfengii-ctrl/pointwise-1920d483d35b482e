import json
import sqlite3
from hashlib import sha256

from archive.integrity import EvidenceMismatch
from archive.records import FixedReading


class EventConflict(ValueError):
    pass


def init_event_store(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS retest_events (
            registration_id TEXT NOT NULL REFERENCES rosters (registration_id),
            event_id TEXT NOT NULL,
            specimen_id TEXT NOT NULL,
            reading TEXT NOT NULL,
            PRIMARY KEY (registration_id, event_id, specimen_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS retest_event_seals (
            registration_id TEXT NOT NULL REFERENCES rosters (registration_id),
            event_id TEXT NOT NULL,
            seal TEXT NOT NULL,
            PRIMARY KEY (registration_id, event_id)
        )
    ''')


def event_seal(registration_id: str, event_id: str, values: dict[str, int]) -> str:
    snapshot = [
        'retest-event-v1',
        registration_id,
        event_id,
        [[specimen, hex(values[specimen])] for specimen in sorted(values)],
    ]
    encoded = json.dumps(snapshot, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return sha256(encoded).hexdigest()


def load_event_values(
    db: sqlite3.Connection, registration_id: str, event_id: str
) -> dict[str, int]:
    rows = db.execute(
        'SELECT specimen_id, reading FROM retest_events '
        'WHERE registration_id = ? AND event_id = ? ORDER BY specimen_id',
        (registration_id, event_id),
    ).fetchall()
    values = {}
    for specimen, value in rows:
        if specimen in values:
            raise EvidenceMismatch(f'duplicate event reading for {event_id!r}/{specimen!r}')
        values[specimen] = int(value, 16)

    seal_row = db.execute(
        'SELECT seal FROM retest_event_seals WHERE registration_id = ? AND event_id = ?',
        (registration_id, event_id),
    ).fetchone()
    if not values and seal_row is None:
        return {}
    if not values or seal_row is None:
        raise EvidenceMismatch(f'fixed event ledger is incomplete for {event_id!r}')
    if event_seal(registration_id, event_id, values) != seal_row[0]:
        raise EvidenceMismatch(f'fixed event ledger differs for {event_id!r}')
    return values


def merge_event_values(
    db: sqlite3.Connection,
    registration_id: str,
    event_id: str,
    readings: tuple[FixedReading, ...],
) -> dict[str, int]:
    existing = load_event_values(db, registration_id, event_id)
    merged = dict(existing)
    for reading in readings:
        prior = merged.get(reading.specimen_id)
        if prior is not None and prior != reading.value:
            raise EventConflict(
                f'event {event_id!r} fixes {reading.specimen_id!r} at {prior}, '
                f'not {reading.value} at {reading.location.as_key()}'
            )
        merged[reading.specimen_id] = reading.value

    new_specimens = sorted(set(merged) - set(existing))
    if not new_specimens:
        return merged
    if existing:
        db.execute(
            'DELETE FROM retest_event_seals WHERE registration_id = ? AND event_id = ?',
            (registration_id, event_id),
        )
    db.executemany(
        'INSERT INTO retest_events (registration_id, event_id, specimen_id, reading) '
        'VALUES (?, ?, ?, ?)',
        (
            (registration_id, event_id, specimen, hex(merged[specimen]))
            for specimen in new_specimens
        ),
    )
    db.execute(
        'INSERT INTO retest_event_seals (registration_id, event_id, seal) VALUES (?, ?, ?)',
        (registration_id, event_id, event_seal(registration_id, event_id, merged)),
    )
    return merged
