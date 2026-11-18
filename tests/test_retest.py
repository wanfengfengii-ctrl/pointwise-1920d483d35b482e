import json
import sqlite3

from archive.events import EventConflict
from archive.integrity import EvidenceMismatch
from archive.readings import get_capture, read_capture_evidence
from intake.accept import import_named, import_retest
from portal.columns import BadRecording
from tests.support import StoreCase, fail_at_commit, page, recording


def retest_recording(page_id, rows, *, event_id='E', batch_id='LOT-17', scale=(1, 10),
                     columns=('specimen_id', 'reading')):
    return json.dumps({
        'batch_id': batch_id,
        'event_id': event_id,
        'source_scale': list(scale),
        'pages': [{'page_id': page_id, 'columns': list(columns), 'rows': list(rows)}],
    }).encode('utf-8')


class RetestImportTests(StoreCase):
    def retest_counts(self):
        return self.db.execute('''
            SELECT (SELECT count(*) FROM capture_receipts),
                   (SELECT count(*) FROM retest_events),
                   (SELECT count(*) FROM retest_event_seals)
        ''').fetchone()

    def test_partial_import_and_order(self):
        registration = self.register()
        raw = retest_recording('R1', [['y', 220], ['x', 200]])
        receipt = import_retest(self.db, registration, raw)
        capture, original = read_capture_evidence(self.db, receipt)
        self.assertEqual(original, raw)
        self.assertEqual(capture.event_id, 'E')
        self.assertEqual((capture.registration_id, capture.version), (registration, 'v1'))
        self.assertEqual(
            [(row.specimen_id, row.value, row.location.as_key()) for row in capture.readings],
            [('x', 20, ('R1', 2)), ('y', 22, ('R1', 1))],
        )

        single = import_retest(self.db, registration, retest_recording('R2', [['x', 200]]))
        only_x, _ = read_capture_evidence(self.db, single)
        self.assertEqual([(row.specimen_id, row.location.as_key()) for row in only_x.readings],
                         [('x', ('R2', 1))])

    def test_complementary_exports_and_replay(self):
        registration = self.register()
        x_raw = retest_recording('PX', [['x', 200]])
        y_raw = retest_recording('PY', [['y', 220]])
        x_receipt = import_retest(self.db, registration, x_raw)
        y_receipt = import_retest(self.db, registration, y_raw)
        self.assertNotEqual(x_receipt, y_receipt)
        self.assertEqual(import_retest(self.db, registration, x_raw), x_receipt)
        self.assertEqual(import_retest(self.db, registration, y_raw), y_receipt)
        self.assertEqual(self.retest_counts(), (2, 2, 1))
        self.reopen_store()
        self.assertEqual(import_retest(self.db, registration, x_raw), x_receipt)
        self.assertEqual(read_capture_evidence(self.db, x_receipt)[1], x_raw)

    def test_overlap_equivalent_across_scale_and_order(self):
        registration = self.register()
        first = import_retest(
            self.db, registration,
            retest_recording('P1', [['x', 200], ['y', 220]]),
        )
        reordered = retest_recording(
            'P2', [[22, 'y'], [20, 'x']], scale=(1, 1),
            columns=('reading', 'specimen_id'),
        )
        second = import_retest(self.db, registration, reordered)
        self.assertNotEqual(first, second)
        capture, _ = read_capture_evidence(self.db, second)
        self.assertEqual([(row.specimen_id, row.value, row.location.as_key())
                          for row in capture.readings],
                         [('x', 20, ('P2', 2)), ('y', 22, ('P2', 1))])
        self.assertEqual(self.retest_counts(), (2, 2, 1))

    def test_conflict_rejects_whole_export_then_partial_accepts(self):
        registration = self.register()
        import_retest(self.db, registration, retest_recording('PX', [['x', 200]]))
        conflict = retest_recording('PC', [['x', 210], ['y', 220]])
        for _ in range(2):
            with self.assertRaises(EventConflict):
                import_retest(self.db, registration, conflict)
        self.assertEqual(self.retest_counts(), (1, 1, 1))
        self.reopen_store()
        later = import_retest(self.db, registration, retest_recording('PY', [['y', 230]]))
        capture, _ = read_capture_evidence(self.db, later)
        self.assertEqual([(row.specimen_id, row.value) for row in capture.readings], [('y', 23)])

    def test_event_identity_exact(self):
        registration = self.register()
        first = import_retest(self.db, registration, retest_recording('A', [['x', 200]], event_id='M'))
        other = import_retest(self.db, registration, retest_recording('B', [['x', 200]], event_id='M '))
        self.assertNotEqual(first, other)
        self.assertEqual(self.retest_counts()[0], 2)
        with self.assertRaises(BadRecording):
            import_retest(self.db, registration, retest_recording('C', [['x', 200]], event_id='   '))
        with self.assertRaises(BadRecording):
            import_retest(self.db, registration, retest_recording('C', [['x', 200]], event_id=None))

    def test_existing_named_original_with_event_is_not_retest(self):
        registration = self.register()
        raw = recording(page('P1', [['x', 200], ['y', 220]]))
        with_id = json.dumps({
            'batch_id': 'LOT-17', 'event_id': 'M', 'source_scale': [1, 10],
            'pages': [{'page_id': 'P1', 'columns': ['specimen_id', 'reading'],
                       'rows': [['x', 200], ['y', 220]]}],
        }).encode('utf-8')
        named = import_named(self.db, registration, with_id)
        self.assertIsNone(get_capture(self.db, named).event_id)
        self.assertEqual(import_named(self.db, registration, with_id), named)
        retest = import_retest(self.db, registration, with_id)
        self.assertNotEqual(named, retest)
        self.assertEqual(get_capture(self.db, retest).event_id, 'M')
        self.assertEqual(import_retest(self.db, registration, with_id), retest)
        self.assertEqual(self.retest_counts(), (2, 2, 1))

    def test_invalid_retest_sources(self):
        registration = self.register()
        cases = {
            'wrong batch': retest_recording('P', [['x', 200]], batch_id='LOT-18'),
            'outside roster': retest_recording('P', [['z', 200]]),
            'duplicate specimen': retest_recording('P', [['x', 200], ['x', 200]]),
            'empty export': retest_recording('P', []),
            'inexact scale': retest_recording('P', [['x', 201]]),
            'bad scale': retest_recording('P', [['x', 200]], scale=(1, 0)),
            'bad json': b'{',
            'missing event': json.dumps({
                'batch_id': 'LOT-17', 'source_scale': [1, 10],
                'pages': [{'page_id': 'P1', 'columns': ['specimen_id', 'reading'],
                           'rows': [['x', 200]]}],
            }).encode('utf-8'),
        }
        for reason, raw in cases.items():
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    import_retest(self.db, registration, raw)
        self.assertEqual(self.retest_counts(), (0, 0, 0))

    def test_commit_failure_and_retry(self):
        registration = self.register()
        old_receipt = import_retest(self.db, registration, retest_recording('P1', [['x', 200]]))
        old_capture = get_capture(self.db, old_receipt)
        new_raw = retest_recording('P2', [['y', 220]])
        pending = []

        def reject_commit(db):
            pending.append(self.retest_counts())
            fail_at_commit(db)

        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            import_retest(self.db, registration, new_raw, before_commit=reject_commit)
        self.assertEqual(pending, [(2, 2, 1)])
        self.reopen_store()
        self.assertEqual(self.retest_counts(), (1, 1, 1))
        self.assertEqual(read_capture_evidence(self.db, old_receipt), (old_capture,
                                                                      retest_recording('P1', [['x', 200]])))
        new_receipt = import_retest(self.db, registration, new_raw)
        self.assertNotEqual(old_receipt, new_receipt)
        self.assertEqual(import_retest(self.db, registration, new_raw), new_receipt)
        self.assertEqual(self.retest_counts(), (2, 2, 1))

    def test_damaged_event_ledger_blocks_replay_and_merge(self):
        registration = self.register()
        raw = retest_recording('P1', [['x', 200]])
        receipt = import_retest(self.db, registration, raw)
        self.db.execute('DELETE FROM retest_events WHERE registration_id = ?', (registration,))
        with self.assertRaises(EvidenceMismatch):
            import_retest(self.db, registration, raw)
        with self.assertRaises(EvidenceMismatch):
            import_retest(self.db, registration, retest_recording('P2', [['y', 220]]))

    def test_damaged_original_blocks_replay(self):
        registration = self.register()
        raw = retest_recording('P1', [['x', 200]])
        receipt = import_retest(self.db, registration, raw)
        self.db.execute('DELETE FROM capture_originals WHERE receipt = ?', (receipt,))
        with self.assertRaises(EvidenceMismatch):
            import_retest(self.db, registration, raw)
        with self.assertRaises(EvidenceMismatch):
            get_capture(self.db, receipt)
