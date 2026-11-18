import json
import sqlite3

from archive.events import EventConflict
from archive.integrity import EvidenceMismatch
from intake.accept import import_named, import_retest
from review.issue import get_report, issue_review, issue_witnessed
from review.storage import UnknownReport
from tests.support import SAMPLES, StoreCase, fail_at_commit, page, recording

RETESTS = json.loads((SAMPLES / 'retests.json').read_text(encoding='utf-8'))


def sample_raw(name: str) -> bytes:
    return json.dumps(RETESTS[name]).encode('utf-8')


class WitnessedIssueTests(StoreCase):
    def import_samples(self, *names):
        registration = self.register()
        receipts = {name: import_retest(self.db, registration, sample_raw(name)) for name in names}
        return registration, receipts

    def witness_rows(self, report):
        return [(item.specimen_id, item.event_id, item.location.as_key())
                for item in report.witnesses]

    def test_primary_with_independent_events(self):
        registration, receipts = self.import_samples('primary', 'p_full', 'q_x')
        report = issue_witnessed(self.db, receipts['primary'], [receipts['p_full'], receipts['q_x']])
        self.assertIsNotNone(report)
        self.assertEqual((report.receipt, report.registration_id, report.version),
                         (receipts['primary'], registration, 'v1'))
        self.assertEqual([(row.specimen_id, row.value, row.limit, row.passed)
                          for row in report.verdicts],
                         [('x', 20, 20, True), ('y', 22, 20, False)])
        self.assertEqual(report.over_limit, ('y',))
        self.assertEqual(self.witness_rows(report),
                         [('x', 'Q', ('Q1', 1)), ('y', 'P', ('P1', 2))])
        self.assertEqual([item.receipt for item in report.witnesses],
                         [receipts['q_x'], receipts['p_full']])
        self.assertEqual(get_report(self.db, report.report_id), report)

    def test_same_event_is_not_independent(self):
        _, receipts = self.import_samples('primary', 'p_full', 'p_reordered')
        self.assertIsNone(
            issue_witnessed(self.db, receipts['primary'], [receipts['p_full'], receipts['p_reordered']])
        )
        self.assertEqual(self.report_ids(), set())

    def test_partial_coverage_needs_both_events(self):
        registration, receipts = self.import_samples('primary', 'p_x', 'p_y', 'q_x')
        self.assertIsNone(
            issue_witnessed(self.db, receipts['primary'], [receipts['p_x'], receipts['q_x']])
        )
        report = issue_witnessed(
            self.db, receipts['primary'], [receipts['p_x'], receipts['q_x'], receipts['p_y']]
        )
        self.assertEqual(self.witness_rows(report),
                         [('x', 'Q', ('Q1', 1)), ('y', 'P', ('PY', 1))])

        import_retest(self.db, registration, sample_raw('p_full'))
        self.reopen_store()
        historical = get_report(self.db, report.report_id)
        self.assertEqual(self.witness_rows(historical),
                         [('x', 'Q', ('Q1', 1)), ('y', 'P', ('PY', 1))])
        self.assertEqual([item.receipt for item in historical.witnesses],
                         [receipts['q_x'], receipts['p_y']])

    def test_duplicate_and_order_insensitive(self):
        _, receipts = self.import_samples('primary', 'p_full', 'q_x')
        report = issue_witnessed(
            self.db, receipts['primary'],
            [receipts['q_x'], receipts['p_full'], receipts['q_x'], receipts['p_full']],
        )
        self.assertIsNotNone(report)
        second = issue_witnessed(
            self.db, receipts['primary'], [receipts['p_full'], receipts['q_x']]
        )
        self.assertEqual(self.witness_rows(report), self.witness_rows(second))

    def test_empty_candidates_returns_none(self):
        _, receipts = self.import_samples('primary')
        self.assertIsNone(issue_witnessed(self.db, receipts['primary'], []))
        self.assertEqual(self.report_ids(), set())

    def test_named_receipt_rejected(self):
        registration, receipts = self.import_samples('primary', 'p_full', 'q_x')
        named = import_named(self.db, registration, recording(page('P1', [['x', 200], ['y', 220]])))
        with self.assertRaises(ValueError):
            issue_witnessed(self.db, named, [receipts['p_full'], receipts['q_x']])
        with self.assertRaises(ValueError):
            issue_witnessed(self.db, receipts['primary'], [named])
        self.assertEqual(self.report_ids(), set())
        self.assertEqual(issue_review(self.db, named).witnesses, ())
        with self.assertRaises(ValueError):
            issue_review(self.db, receipts['primary'])

    def test_incomplete_primary_rejected(self):
        _, receipts = self.import_samples('p_x', 'q_x', 'p_y')
        with self.assertRaises(ValueError):
            issue_witnessed(self.db, receipts['p_x'], [receipts['q_x'], receipts['p_y']])
        self.assertEqual(self.report_ids(), set())

    def test_cross_registration_rejected(self):
        other = self.register(version='v2', limit=25)
        primary_registration = self.register()
        primary = import_retest(self.db, primary_registration, sample_raw('primary'))
        own = import_retest(self.db, primary_registration, sample_raw('p_full'))
        foreign = import_retest(self.db, other, sample_raw('q_x'))
        with self.assertRaises(ValueError):
            issue_witnessed(self.db, primary, [own, foreign])
        self.assertEqual(self.report_ids(), set())

    def test_commit_failure_keeps_old_result(self):
        _, receipts = self.import_samples('primary', 'p_full', 'q_x')
        old = issue_witnessed(self.db, receipts['primary'], [receipts['p_full'], receipts['q_x']])
        pending = []

        def reject(db):
            pending.append(self.report_ids())
            fail_at_commit(db)

        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            issue_witnessed(self.db, receipts['primary'],
                            [receipts['p_full'], receipts['q_x']], before_commit=reject)
        self.assertEqual(len(pending), 1)
        self.assertIn(old.report_id, pending[0])
        self.reopen_store()
        self.assertEqual(self.report_ids(), {old.report_id})
        self.assertEqual(get_report(self.db, old.report_id), old)
        retry = issue_witnessed(self.db, receipts['primary'], [receipts['p_full'], receipts['q_x']])
        self.assertEqual(self.witness_rows(retry), self.witness_rows(old))
        self.assertEqual(get_report(self.db, retry.report_id), retry)

    def test_damaged_selected_evidence_blocks_claim(self):
        _, receipts = self.import_samples('primary', 'p_full', 'q_x')
        report = issue_witnessed(self.db, receipts['primary'], [receipts['p_full'], receipts['q_x']])
        chosen = {item.specimen_id: item.receipt for item in report.witnesses}
        self.db.execute('DELETE FROM capture_originals WHERE receipt = ?', (chosen['y'],))
        self.reopen_store()
        with self.assertRaises(EvidenceMismatch):
            get_report(self.db, report.report_id)

    def test_damaged_primary_evidence_blocks_claim(self):
        _, receipts = self.import_samples('primary', 'p_full', 'q_x')
        report = issue_witnessed(self.db, receipts['primary'], [receipts['p_full'], receipts['q_x']])
        self.db.execute('UPDATE capture_readings SET reading = ? WHERE receipt = ? AND specimen_id = ?',
                        (hex(21), receipts['primary'], 'y'))
        self.reopen_store()
        with self.assertRaises(EvidenceMismatch):
            get_report(self.db, report.report_id)

    def test_tampered_witness_snapshot_blocks_claim(self):
        _, receipts = self.import_samples('primary', 'p_full', 'q_x')
        report = issue_witnessed(self.db, receipts['primary'], [receipts['p_full'], receipts['q_x']])
        saved = json.loads(self.db.execute(
            'SELECT snapshot FROM review_reports WHERE report_id = ?', (report.report_id,)
        ).fetchone()[0])
        saved['witnesses'][0][4] = 2
        self.db.execute('UPDATE review_reports SET snapshot = ? WHERE report_id = ?',
                        (json.dumps(saved), report.report_id))
        self.reopen_store()
        with self.assertRaises(EvidenceMismatch):
            get_report(self.db, report.report_id)

    def test_new_export_cannot_change_claim(self):
        registration, receipts = self.import_samples('primary', 'p_x', 'p_y', 'q_x')
        report = issue_witnessed(self.db, receipts['primary'],
                                 [receipts['p_x'], receipts['q_x'], receipts['p_y']])
        before = get_report(self.db, report.report_id)
        import_retest(self.db, registration, sample_raw('p_full'))
        self.reopen_store()
        after = get_report(self.db, report.report_id)
        self.assertEqual(after, before)
        self.assertEqual([w.location.as_key() for w in after.witnesses], [('Q1', 1), ('PY', 1)])

    def test_unknown_receipts_and_reports(self):
        _, receipts = self.import_samples('primary', 'p_full', 'q_x')
        with self.assertRaises(KeyError):
            issue_witnessed(self.db, 'unknown', [receipts['p_full']])
        with self.assertRaises(KeyError):
            issue_witnessed(self.db, receipts['primary'], ['unknown'])
        with self.assertRaises(UnknownReport):
            get_report(self.db, 'unknown-report')
        self.assertEqual(self.report_ids(), set())

    def test_conflicting_candidate_cannot_enter_event(self):
        registration, receipts = self.import_samples('primary', 'p_x')
        with self.assertRaises(EventConflict):
            import_retest(self.db, registration, sample_raw('p_conflict'))

    def test_different_value_candidate_is_not_selected(self):
        registration, receipts = self.import_samples('primary', 'p_full', 'q_x')
        off_value = json.dumps({
            'batch_id': 'LOT-17', 'event_id': 'R', 'source_scale': [1, 10],
            'pages': [{'page_id': 'R1', 'columns': ['specimen_id', 'reading'],
                       'rows': [['x', 190]]}],
        }).encode('utf-8')
        r_x = import_retest(self.db, registration, off_value)
        report = issue_witnessed(self.db, receipts['primary'], [receipts['p_full'], r_x])
        self.assertIsNone(report)
        self.assertEqual(self.report_ids(), set())

    def test_claim_after_source_closed_and_reopened(self):
        from portal.source import PortalClosed, RecordedPortal

        registration, receipts = self.import_samples('primary', 'p_full', 'q_x')
        report = issue_witnessed(self.db, receipts['primary'], [receipts['p_full'], receipts['q_x']])
        portal = RecordedPortal({'LOT-17': SAMPLES / 'retests.json'})
        portal.close()
        with self.assertRaises(PortalClosed):
            portal.fetch_recording('LOT-17')
        self.reopen_store()
        claimed = get_report(self.db, report.report_id)
        self.assertEqual(claimed, report)
        self.assertEqual([(w.event_id, w.location.as_key()) for w in claimed.witnesses],
                         [('Q', ('Q1', 1)), ('P', ('P1', 2))])
