import sqlite3
from dataclasses import dataclass

from archive.readings import NamedCapture, read_capture_evidence
from portal.evidence import SourceLocation
from review.report import Witness
from runs.roster import Roster


class RetestEligibilityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateRow:
    event_id: str
    receipt: str
    location: SourceLocation
    value: int


def dedupe_receipts(receipts: object) -> tuple[str, ...]:
    if isinstance(receipts, (str, bytes)) or not hasattr(receipts, '__iter__'):
        raise RetestEligibilityError('candidate_receipts must be a sequence of receipt ids')
    ordered: list[str] = []
    for receipt in receipts:
        if not isinstance(receipt, str):
            raise RetestEligibilityError('candidate receipts must be strings')
        if receipt not in ordered:
            ordered.append(receipt)
    return tuple(ordered)


def load_retest_evidence(
    db: sqlite3.Connection, receipt: str, registration_id: str
) -> NamedCapture:
    capture, _ = read_capture_evidence(db, receipt)
    if not capture.is_retest:
        raise RetestEligibilityError(
            f'named capture {receipt!r} cannot be used in a witnessed retest review'
        )
    if capture.registration_id != registration_id:
        raise RetestEligibilityError(
            f'capture {receipt!r} is fixed under {capture.registration_id!r}, '
            f'not {registration_id!r}'
        )
    return capture


def require_full_primary(primary: NamedCapture, roster: Roster) -> None:
    specimens = tuple(row.specimen_id for row in primary.readings)
    if specimens != roster.specimens:
        raise RetestEligibilityError(
            f'primary capture {primary.receipt!r} does not cover the full roster'
        )


def _assign(
    specimens: tuple[str, ...], eligible: dict[str, tuple[str, ...]]
) -> dict[str, str] | None:
    match_event: dict[str, str] = {}

    def augment(specimen: str, visited: set[str]) -> bool:
        for event in eligible[specimen]:
            if event in visited:
                continue
            visited.add(event)
            holder = match_event.get(event)
            if holder is None or augment(holder, visited):
                match_event[event] = specimen
                return True
        return False

    for specimen in specimens:
        if not augment(specimen, set()):
            return None
    return match_event


def select_witnesses(
    primary: NamedCapture,
    candidates: tuple[NamedCapture, ...],
    roster: Roster,
) -> tuple[Witness, ...] | None:
    primary_values = {row.specimen_id: row.value for row in primary.readings}

    rows_by_specimen: dict[str, list[CandidateRow]] = {
        specimen: [] for specimen in roster.specimens
    }
    for capture in candidates:
        event_id = capture.event_id
        if event_id is None or event_id == primary.event_id:
            continue
        for row in capture.readings:
            rows = rows_by_specimen.get(row.specimen_id)
            if rows is None or row.value != primary_values[row.specimen_id]:
                continue
            rows.append(
                CandidateRow(event_id, capture.receipt, row.location, row.value)
            )

    eligible: dict[str, tuple[str, ...]] = {}
    for specimen in roster.specimens:
        seen: list[str] = []
        for row in rows_by_specimen[specimen]:
            if row.event_id not in seen:
                seen.append(row.event_id)
        eligible[specimen] = tuple(seen)

    assignment = _assign(roster.specimens, eligible)
    if assignment is None:
        return None

    chosen: dict[str, CandidateRow] = {}
    for event_id, specimen in assignment.items():
        for row in rows_by_specimen[specimen]:
            if row.event_id == event_id:
                chosen[specimen] = row
                break

    return tuple(
        Witness(
            specimen,
            chosen[specimen].event_id,
            chosen[specimen].receipt,
            chosen[specimen].location,
        )
        for specimen in roster.specimens
    )
