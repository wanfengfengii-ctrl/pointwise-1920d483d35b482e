from dataclasses import dataclass, field
from uuid import uuid4

from portal.evidence import SourceLocation


@dataclass(frozen=True, slots=True)
class SpecimenVerdict:
    specimen_id: str
    value: int
    limit: int
    passed: bool


@dataclass(frozen=True, slots=True)
class Witness:
    specimen_id: str
    event_id: str
    receipt: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class ReviewReport:
    report_id: str
    receipt: str
    registration_id: str
    batch_id: str
    version: str
    verdicts: tuple[SpecimenVerdict, ...]
    over_limit: tuple[str, ...]
    witnesses: tuple[Witness, ...] = field(default_factory=tuple)

    @property
    def is_witnessed(self) -> bool:
        return bool(self.witnesses)


def build_report(
    receipt: str,
    registration_id: str,
    batch_id: str,
    version: str,
    verdicts: tuple[SpecimenVerdict, ...],
) -> ReviewReport:
    return ReviewReport(
        report_id=f'qc-{uuid4().hex}',
        receipt=receipt,
        registration_id=registration_id,
        batch_id=batch_id,
        version=version,
        verdicts=verdicts,
        over_limit=tuple(verdict.specimen_id for verdict in verdicts if not verdict.passed),
    )


def build_witnessed_report(
    receipt: str,
    registration_id: str,
    batch_id: str,
    version: str,
    verdicts: tuple[SpecimenVerdict, ...],
    witnesses: tuple[Witness, ...],
) -> ReviewReport:
    return ReviewReport(
        report_id=f'qc-{uuid4().hex}',
        receipt=receipt,
        registration_id=registration_id,
        batch_id=batch_id,
        version=version,
        verdicts=verdicts,
        over_limit=tuple(verdict.specimen_id for verdict in verdicts if not verdict.passed),
        witnesses=witnesses,
    )
