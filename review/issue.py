import sqlite3

from archive.database import BeforeCommit
from archive.integrity import EvidenceMismatch
from archive.readings import NamedCapture, UnknownCapture, get_capture, read_capture_evidence
from review.evaluate import evaluate_readings
from review.report import ReviewReport, build_report, build_witnessed_report
from review.storage import load_report, save_report
from review.witness import (
    dedupe_receipts,
    load_retest_evidence,
    require_full_primary,
    select_witnesses,
)
from runs.registry import get_roster


def issue_review(
    db: sqlite3.Connection, receipt: str, *, before_commit: BeforeCommit | None = None
) -> ReviewReport:
    capture = get_capture(db, receipt)
    if capture.is_retest:
        raise ValueError(
            f'retest capture {receipt!r} must be issued through issue_witnessed'
        )
    registered = get_roster(db, capture.registration_id)
    verdicts = evaluate_readings(capture, registered)
    report = build_report(
        capture.receipt,
        registered.registration_id,
        registered.roster.batch_id,
        registered.roster.version,
        verdicts,
    )
    save_report(db, report, before_commit=before_commit)
    return report


def issue_witnessed(
    db: sqlite3.Connection,
    primary_receipt: str,
    candidate_receipts: object,
    *,
    before_commit: BeforeCommit | None = None,
) -> ReviewReport | None:
    wanted = dedupe_receipts(candidate_receipts)

    primary, _ = read_capture_evidence(db, primary_receipt)
    registered = get_roster(db, primary.registration_id)
    roster = registered.roster

    if not primary.is_retest:
        raise ValueError(f'primary receipt {primary_receipt!r} is not a retest capture')
    require_full_primary(primary, roster)

    candidates: list[NamedCapture] = []
    for receipt in wanted:
        candidates.append(load_retest_evidence(db, receipt, registered.registration_id))

    witnesses = select_witnesses(primary, tuple(candidates), roster)
    if witnesses is None:
        return None

    verdicts = evaluate_readings(primary, registered)
    report = build_witnessed_report(
        primary.receipt,
        registered.registration_id,
        roster.batch_id,
        roster.version,
        verdicts,
        witnesses,
    )
    save_report(db, report, before_commit=before_commit)
    return report


def _verify_primary(report: ReviewReport, capture: NamedCapture) -> None:
    if not capture.is_retest:
        raise EvidenceMismatch(f'report {report.report_id} is bound to a named capture')
    if capture.registration_id != report.registration_id:
        raise EvidenceMismatch(f'primary registration differs for {report.report_id}')
    if capture.version != report.version:
        raise EvidenceMismatch(f'primary version differs for {report.report_id}')
    fixed = [(row.specimen_id, row.value) for row in capture.readings]
    stated = [(row.specimen_id, row.value) for row in report.verdicts]
    if fixed != stated:
        raise EvidenceMismatch(f'primary readings differ for {report.report_id}')


def _fixed_evidence(db: sqlite3.Connection, receipt: str, report_id: str) -> NamedCapture:
    try:
        capture, _ = read_capture_evidence(db, receipt)
    except UnknownCapture as error:
        raise EvidenceMismatch(f'fixed evidence missing for {report_id}') from error
    return capture


def get_report(db: sqlite3.Connection, report_id: str) -> ReviewReport:
    report = load_report(db, report_id)
    if not report.witnesses:
        return report
    primary = _fixed_evidence(db, report.receipt, report_id)
    _verify_primary(report, primary)
    values = {row.specimen_id: row.value for row in report.verdicts}
    seen_specimens: set[str] = set()
    seen_events: set[str] = set()
    for witness in report.witnesses:
        if witness.specimen_id not in values or witness.specimen_id in seen_specimens:
            raise EvidenceMismatch(f'witness specimen differs for {report_id}')
        if witness.event_id == primary.event_id or witness.event_id in seen_events:
            raise EvidenceMismatch(f'witness event differs for {report_id}')
        seen_specimens.add(witness.specimen_id)
        seen_events.add(witness.event_id)
        capture = _fixed_evidence(db, witness.receipt, report_id)
        if capture.registration_id != report.registration_id:
            raise EvidenceMismatch(f'witness registration differs for {report_id}')
        if capture.event_id != witness.event_id:
            raise EvidenceMismatch(f'witness event differs for {report_id}')
        if not any(
            row.specimen_id == witness.specimen_id
            and row.location == witness.location
            and row.value == values.get(witness.specimen_id)
            for row in capture.readings
        ):
            raise EvidenceMismatch(f'witness evidence differs for {report_id}')
    return report
