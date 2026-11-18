import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.database import init_archive, open_archive
from archive.readings import read_capture_evidence
from intake.accept import import_named, import_retest
from intake.coverage import CoverageError
from portal.recording import decode_named_recording
from portal.source import RecordedPortal
from review.issue import get_report, issue_review, issue_witnessed
from review.storage import init_report_store
from runs.registry import register
from runs.storage import init_roster_store


def initialize(db) -> None:
    init_roster_store(db)
    init_archive(db)
    init_report_store(db)


def named_demo(samples: Path, plan: dict) -> None:
    batch_id = plan['batch_id']
    with TemporaryDirectory(prefix='lab-handoff-') as temporary:
        database = Path(temporary) / 'handoff.sqlite3'
        with closing(open_archive(database)) as db:
            initialize(db)
            registration = register(db, **plan)
            incomplete = RecordedPortal({batch_id: samples / 'named-incomplete.json'})
            try:
                import_named(db, registration, incomplete.fetch_recording(batch_id))
            except CoverageError as error:
                print(f'缺片录制未发布。{error}')
            else:
                raise AssertionError('incomplete recording was published')
            finally:
                incomplete.close()

            portal = RecordedPortal({batch_id: samples / 'named-complete.json'})
            try:
                receipt = import_named(db, registration, portal.fetch_recording(batch_id))
            finally:
                portal.close()

        with closing(open_archive(database)) as db:
            initialize(db)
            capture, original = read_capture_evidence(db, receipt)
            report = get_report(db, issue_review(db, receipt).report_id)

        recording = decode_named_recording(original)
        source_rows = {row.location.as_key(): row for row in recording.rows}
        actual = [
            [row.specimen_id, row.value, *row.location.as_key(), source_rows[row.location.as_key()].specimen_id]
            for row in capture.readings
        ]
        expected = [['x', 20, 'P1', 2, 'x'], ['y', 22, 'P1', 1, 'y']]
        downstream_actual = {
            'verdicts': [[row.specimen_id, row.value, row.limit, row.passed] for row in report.verdicts],
            'over_limit': list(report.over_limit),
            'receipt_bound': report.receipt == receipt and report.registration_id == registration,
            'version': report.version,
        }
        downstream_expected = {
            'verdicts': [['x', 20, 20, True], ['y', 22, 20, False]],
            'over_limit': ['y'],
            'receipt_bound': True,
            'version': 'v1',
        }
        if actual != expected or downstream_actual != downstream_expected:
            raise AssertionError((actual, downstream_actual))
        for verdict in report.verdicts:
            print(f'{verdict.specimen_id}={verdict.value}，上限 {verdict.limit}，'
                  f'{"通过" if verdict.passed else "越限"}')
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v7',
            'input': {'plan': plan, 'source_scale': list(recording.source_scale),
                      'source_rows': [[row.specimen_id, row.raw_reading, *row.location.as_key()]
                                      for row in recording.rows]},
            'expected': expected,
            'actual': actual,
            'downstream_expected': downstream_expected,
            'downstream_actual': downstream_actual,
            'code_paths': ['portal/pages.py', 'intake/accept.py', 'intake/scales.py',
                           'archive/readings.py', 'review/issue.py', 'review/evaluate.py'],
            'oracle': '按固定样例手算 200/10=20、220/10=22，核对 P1 第 2、1 行和上限 20。',
        }, ensure_ascii=False, separators=(',', ':')))


def _prepared_sources(directory: Path, retests: dict, names: tuple[str, ...]) -> dict[str, Path]:
    directory.mkdir()
    paths = {}
    for name in names:
        path = directory / f'{name}.json'
        path.write_bytes(json.dumps(retests[name]).encode('utf-8'))
        paths[name] = path
    return paths


def witness_demo(samples: Path, plan: dict, retests: dict) -> None:
    names = ('primary', 'p_full', 'p_reordered', 'q_x')
    with TemporaryDirectory(prefix='lab-witness-') as temporary:
        root = Path(temporary)
        database = root / 'handoff.sqlite3'
        sources = _prepared_sources(root / 'portal-export', retests, names)

        with closing(open_archive(database)) as db:
            initialize(db)
            registration = register(db, **plan)
            portal = RecordedPortal({name: path for name, path in sources.items()})
            try:
                receipts = {
                    name: import_retest(db, registration, portal.fetch_recording(name))
                    for name in ('primary', 'p_full', 'p_reordered')
                }
                same_event = issue_witnessed(
                    db, receipts['primary'],
                    [receipts['p_full'], receipts['p_reordered']],
                )
                if same_event is not None:
                    raise AssertionError('same-event re-exports were accepted as witnesses')
                print('同事件重复导出不足以签发：两个候选都属于事件 P，'
                      '每个事件至多见证一片，无完整分配，返回 None。')
                receipts['q_x'] = import_retest(
                    db, registration, portal.fetch_recording('q_x')
                )
            finally:
                portal.close()

        with closing(open_archive(database)) as db:
            initialize(db)
            report = issue_witnessed(
                db, receipts['primary'], [receipts['p_full'], receipts['q_x']]
            )
            if report is None:
                raise AssertionError('independent retest witnesses did not complete an assignment')

        with closing(open_archive(database)) as db:
            initialize(db)
            replayed = get_report(db, report.report_id)
            if replayed != report:
                raise AssertionError((report, replayed))

            traces = []
            for verdict, witness in zip(report.verdicts, replayed.witnesses, strict=True):
                fresh, original = read_capture_evidence(db, witness.receipt)
                source = [row for row in decode_named_recording(original).rows
                          if row.location == witness.location]
                if len(source) != 1 or source[0].specimen_id != verdict.specimen_id:
                    raise AssertionError((verdict, witness, source))
                traces.append([
                    verdict.specimen_id, verdict.value, witness.event_id, witness.receipt,
                    *witness.location.as_key(), source[0].raw_reading,
                ])
                print(f'{verdict.specimen_id}={verdict.value}，上限 {verdict.limit}，'
                      f'{"通过" if verdict.passed else "越限"}；见证事件 {witness.event_id}，'
                      f'回执 {witness.receipt}，原页行 {witness.location.page_id} 第 '
                      f'{witness.location.row_number} 行。')

        expected_traces = [
            ['x', 20, 'Q', receipts['q_x'], 'Q1', 1, 20],
            ['y', 22, 'P', receipts['p_full'], 'P1', 2, 220],
        ]
        downstream_actual = {
            'report_id': replayed.report_id,
            'primary_receipt': replayed.receipt == receipts['primary'],
            'registration_bound': replayed.registration_id == registration,
            'verdicts': [[row.specimen_id, row.value, row.limit, row.passed]
                         for row in replayed.verdicts],
            'over_limit': list(replayed.over_limit),
            'witnesses': [[w.specimen_id, w.event_id, w.receipt, *w.location.as_key()]
                          for w in replayed.witnesses],
            'reopened_claim_matches': replayed == report,
        }
        expected_downstream = {
            'report_id': report.report_id,
            'primary_receipt': True,
            'registration_bound': True,
            'verdicts': [['x', 20, 20, True], ['y', 22, 20, False]],
            'over_limit': ['y'],
            'witnesses': [
                ['x', 'Q', receipts['q_x'], 'Q1', 1],
                ['y', 'P', receipts['p_full'], 'P1', 2],
            ],
            'reopened_claim_matches': True,
        }
        if traces != expected_traces or downstream_actual != expected_downstream:
            raise AssertionError((traces, downstream_actual))
        print('交接说明：主回执固定整册裁决；每个其他事件只有一个见证名额，'
              '因此 x 采用事件 Q 的 Q1 第 1 行、y 采用事件 P 的 P1 第 2 行。'
              '报告按报告号固定这两个实际回执与页行，后续新增导出不会改动领取结果。')
        print('WITNESSED_SCENARIO ' + json.dumps({
            'case_id': 'w1',
            'input': {
                'plan': plan,
                'primary': retests['primary'],
                'candidates': [retests['p_full'], retests['q_x']],
                'same_event_only': [retests['p_full'], retests['p_reordered']],
            },
            'expected': expected_traces,
            'actual': traces,
            'downstream_expected': expected_downstream,
            'downstream_actual': downstream_actual,
            'code_paths': ['portal/retest.py', 'intake/accept.py', 'archive/events.py',
                           'archive/integrity.py', 'review/witness.py', 'review/issue.py',
                           'review/storage.py'],
            'oracle': '主事件 M 供整册读数；X 由事件 Q 的 Q1 第 1 行见证、Y 由事件 P 的 '
                      'P1 第 2 行见证，按每事件一片的配额核对。',
        }, ensure_ascii=False, separators=(',', ':')))


def main() -> None:
    samples = Path(__file__).resolve().parent / 'samples'
    plan = json.loads((samples / 'roster.json').read_text(encoding='utf-8'))
    retests = json.loads((samples / 'retests.json').read_text(encoding='utf-8'))
    named_demo(samples, plan)
    witness_demo(samples, plan, retests)


if __name__ == '__main__':
    main()
