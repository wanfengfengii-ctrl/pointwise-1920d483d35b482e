from portal.pages import NamedPageRow
from runs.roster import Roster


class CoverageError(ValueError):
    pass


def order_covered_rows(roster: Roster, rows: tuple[NamedPageRow, ...]) -> tuple[NamedPageRow, ...]:
    allowed = set(roster.specimens)
    by_specimen = {}
    for row in rows:
        specimen = row.specimen_id
        if specimen not in allowed:
            raise CoverageError(f'specimen outside roster: {specimen!r} at {row.location.as_key()}')
        if specimen in by_specimen:
            first = by_specimen[specimen].location.as_key()
            raise CoverageError(f'duplicate reading for {specimen!r} at {first} and {row.location.as_key()}')
        by_specimen[specimen] = row

    missing = [specimen for specimen in roster.specimens if specimen not in by_specimen]
    if missing:
        raise CoverageError(f'missing readings for {missing!r}')
    return tuple(by_specimen[specimen] for specimen in roster.specimens)


def order_partial_rows(roster: Roster, rows: tuple[NamedPageRow, ...]) -> tuple[NamedPageRow, ...]:
    allowed = set(roster.specimens)
    by_specimen = {}
    for row in rows:
        specimen = row.specimen_id
        if specimen not in allowed:
            raise CoverageError(f'specimen outside roster: {specimen!r} at {row.location.as_key()}')
        if specimen in by_specimen:
            first = by_specimen[specimen].location.as_key()
            raise CoverageError(f'duplicate reading for {specimen!r} at {first} and {row.location.as_key()}')
        by_specimen[specimen] = row

    if not by_specimen:
        raise CoverageError('retest recording must contain at least one reading')
    return tuple(by_specimen[specimen] for specimen in roster.specimens if specimen in by_specimen)
