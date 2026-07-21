from pydal import DAL, Field
from pydal.backends.postgres import PostgresDialect

from ._adapt import DEFAULT_URI, IS_POSTGRESQL, drop
from ._compat import unittest


class TestIndexesBasic(unittest.TestCase):
    def testRun(self):
        db = DAL(DEFAULT_URI, check_reserved=["all"])
        db.define_table("tt", Field("aa"))
        rv = db.tt.create_index("idx_aa", db.tt.aa)
        self.assertTrue(rv)
        rv = db.tt.drop_index("idx_aa")
        self.assertTrue(rv)
        with self.assertRaises(Exception):
            db.tt.drop_index("idx_aa")
        db.rollback()
        drop(db.tt)


class TestPostgresIndexSQL(unittest.TestCase):
    def setUp(self):
        self.db = DAL("sqlite:memory", entity_quoting=True)
        self.db.define_table("tt", Field("aa"), Field("bb"), Field("cc"))
        self.table = self.db.tt
        self.dialect = PostgresDialect(self.db._adapter)

    def tearDown(self):
        self.table.drop()

    def test_legacy_sql_is_unchanged(self):
        sql = self.dialect.create_index("idx_aa", self.table, [self.table.aa])
        self.assertEqual(sql, 'CREATE INDEX "idx_aa" ON "tt" ("aa");')
        sql = self.dialect.create_index("idx aa", self.table, [self.table.aa])
        self.assertEqual(sql, 'CREATE INDEX "idx aa" ON "tt" ("aa");')
        sql = self.dialect.create_index(
            "idx_aa_unique",
            self.table,
            [self.table.aa],
            unique=True,
            where='("tt"."bb" IS NOT NULL)',
        )
        self.assertEqual(
            sql,
            'CREATE UNIQUE INDEX "idx_aa_unique" ON "tt" ("aa") '
            'WHERE ("tt"."bb" IS NOT NULL);',
        )

    def test_postgres_options_sql(self):
        sql = self.dialect.create_index(
            "idx_aa_and_bb",
            self.table,
            [self.table.aa, self.table.bb],
            unique=True,
            using="btree",
            opclasses=["text_pattern_ops", None],
            include=[self.table.cc],
            if_not_exists=True,
            concurrently=True,
        )
        self.assertEqual(
            sql,
            'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "idx_aa_and_bb" '
            'ON "tt" USING btree ("aa" text_pattern_ops,"bb") '
            'INCLUDE ("cc");',
        )

    def test_postgres_options_reject_unsafe_values(self):
        original_expand = self.db._adapter.expand
        invalid_options = (
            {"using": "btree; DROP TABLE tt"},
            {"opclasses": ["text_pattern_ops) WHERE TRUE; --"]},
            {"include": ["cc) WHERE TRUE; --"]},
            {"if_not_exists": "yes"},
            {"concurrently": "yes"},
        )
        for options in invalid_options:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    self.dialect.create_index(
                        "idx_aa", self.table, [self.table.aa], **options
                    )
                self.assertEqual(self.db._adapter.expand, original_expand)

        with self.assertRaises(ValueError):
            self.dialect.create_index(
                "idx_aa",
                self.table,
                [self.table.aa],
                opclasses=[None, None],
            )
        with self.assertRaises(ValueError):
            self.dialect.create_index(
                'idx_aa"; DROP TABLE tt; --', self.table, [self.table.aa]
            )


@unittest.skipUnless(IS_POSTGRESQL, "Expressions in indexes are not supported")
class TestIndexesExpressions(unittest.TestCase):
    def testRun(self):
        db = DAL(DEFAULT_URI, check_reserved=["all"], entity_quoting=True)
        db.define_table("tt", Field("aa"), Field("bb", "datetime"))
        sql = db._adapter.dialect.create_index(
            "idx_aa_and_bb", db.tt, [db.tt.aa, db.tt.bb.coalesce(None)]
        )
        with db._adapter.index_expander():
            coalesce_sql = str(db.tt.bb.coalesce(None))
        expected_sql = "CREATE INDEX %s ON %s (%s,%s);" % (
            db._adapter.dialect.quote("idx_aa_and_bb"),
            db.tt.sql_shortref,
            db.tt.aa.sqlsafe_name,
            coalesce_sql,
        )
        self.assertEqual(sql, expected_sql)
        rv = db.tt.create_index("idx_aa_and_bb", db.tt.aa, db.tt.bb.coalesce(None))
        self.assertTrue(rv)
        rv = db.tt.drop_index("idx_aa_and_bb")
        self.assertTrue(rv)
        drop(db.tt)


@unittest.skipUnless(IS_POSTGRESQL, "Partial indexes are not supported")
class TestIndexesWhere(unittest.TestCase):
    def testRun(self):
        db = DAL(DEFAULT_URI, check_reserved=["all"], entity_quoting=True)
        db.define_table("tt", Field("aa"), Field("bb", "boolean"))
        sql = db._adapter.dialect.create_index(
            "idx_aa_f", db.tt, [db.tt.aa], where=str(db.tt.bb == False)
        )
        self.assertEqual(
            sql, 'CREATE INDEX "idx_aa_f" ON "tt" ("aa") WHERE ("tt"."bb" = \'F\');'
        )
        rv = db.tt.create_index("idx_aa_f", db.tt.aa, where=(db.tt.bb == False))
        self.assertTrue(rv)
        rv = db.tt.drop_index("idx_aa_f")
        self.assertTrue(rv)
        drop(db.tt)


@unittest.skipUnless(IS_POSTGRESQL, "PostgreSQL index options are not supported")
class TestPostgresIndexOptions(unittest.TestCase):
    def testRun(self):
        db = DAL(DEFAULT_URI, check_reserved=["all"], entity_quoting=True)
        db.define_table("tt_index_options", Field("aa"), Field("payload"))
        table = db.tt_index_options
        try:
            connection = db._adapter.connection
            connection = getattr(connection, "__wrapped__", connection)
            server_version = getattr(connection, "server_version", 0)
            options = {
                "using": "btree",
                "opclasses": ["text_pattern_ops"],
            }
            if server_version >= 90500:
                options["if_not_exists"] = True
            if server_version >= 110000:
                options["include"] = [table.payload]

            self.assertTrue(
                table.create_index("idx_pg_options", table.aa, **options)
            )
            if server_version >= 90500:
                self.assertTrue(
                    table.create_index("idx_pg_options", table.aa, **options)
                )
            self.assertTrue(table.drop_index("idx_pg_options"))

            if db._adapter.driver_name == "psycopg2":
                previous_autocommit = connection.autocommit
                db.executesql("SELECT 1")
                with self.assertRaises(RuntimeError):
                    table.create_index(
                        "idx_pg_concurrently", table.aa, concurrently=True
                    )
                self.assertEqual(connection.autocommit, previous_autocommit)
                db.rollback()

                self.assertTrue(
                    table.create_index(
                        "idx_pg_concurrently", table.aa, concurrently=True
                    )
                )
                self.assertEqual(connection.autocommit, previous_autocommit)
                with self.assertRaises(RuntimeError):
                    table.create_index(
                        "idx_pg_concurrently", table.aa, concurrently=True
                    )
                self.assertEqual(connection.autocommit, previous_autocommit)
                self.assertTrue(table.drop_index("idx_pg_concurrently"))
        finally:
            db.rollback()
            drop(table)
