"""Category IDs from list_categories must filter the same product memberships."""
import gzip
import io
import json
import os
from pathlib import Path
import re
import unittest

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get('TOOLATHLON_TEST_PG_DSN')


@unittest.skipUnless(DSN, 'Set TOOLATHLON_TEST_PG_DSN to a disposable PostgreSQL database')
class WooCommerceCategoryTest(unittest.TestCase):
    def test_seed_normalization_preserves_membership_and_is_idempotent(self):
        with gzip.open(ROOT / 'db/init.sql.gz', 'rt') as f:
            seed = f.read()
        conn = psycopg2.connect(DSN)
        conn.set_client_encoding("UTF8")
        self.addCleanup(conn.close)
        with conn.cursor() as cur:
            cur.execute('CREATE SCHEMA wc')
            for name in ('wc.product_categories', 'wc.products'):
                cur.execute(re.search(rf'CREATE TABLE {re.escape(name)} \(.*?\);', seed, re.S)[0])
                data = re.search(rf'(COPY {re.escape(name)} .*? FROM stdin;)\n(.*?)\n\\\.', seed, re.S)
                cur.copy_expert(data[1], io.StringIO(data[2] + '\n'))
            cur.execute("SELECT id, categories FROM wc.products ORDER BY id")
            before = cur.fetchall()
            fix = (ROOT / 'db/normalize_wc_categories.sql').read_text()
            cur.execute(fix)
            self.assertGreater(cur.rowcount, 0)
            cur.execute("SELECT id, categories FROM wc.products ORDER BY id")
            after = cur.fetchall()
            self.assertEqual([(i, [c['name'] for c in cs]) for i, cs in before],
                             [(i, [c['name'] for c in cs]) for i, cs in after])
            cur.execute('SELECT id, name FROM wc.product_categories')
            for category_id, name in cur.fetchall():
                expected = [i for i, cs in before if any(c['name'] == name for c in cs)]
                cur.execute('SELECT id FROM wc.products WHERE categories @> %s::jsonb ORDER BY id',
                            (json.dumps([{'id': category_id}]),))
                self.assertEqual([r[0] for r in cur.fetchall()], expected, name)
            cur.execute(fix)
            self.assertEqual(cur.rowcount, 0)
            conn.rollback()


if __name__ == '__main__':
    unittest.main()
