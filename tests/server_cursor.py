from unittest.mock import MagicMock

from pydal import DAL, Field
from pydal.backends.postgres import PostgresPsyco
from pydal.compilers.sql import ParamSQL
from pydal.driver import Driver
from pydal.helpers.classes import ExecutionHandler

from ._compat import unittest


class TestPostgresServerCursor(unittest.TestCase):
    def _adapter(self, adapter_args=None, execute_error=None):
        adapter = PostgresPsyco.__new__(PostgresPsyco)
        adapter.adapter_args = adapter_args or {}
        adapter.execution_handlers = []
        adapter.pool_size = 0
        adapter.uri = "postgres://mock"
        adapter.check_active_connection = False

        connection = MagicMock(name="connection")
        ordinary_cursor = MagicMock(name="ordinary_cursor")
        server_cursors = []
        cursor_calls = []

        def cursor_factory(*args, **kwargs):
            if not kwargs:
                return ordinary_cursor
            cursor_calls.append(kwargs)
            cursor = MagicMock(name="server_cursor")
            cursor.name = kwargs["name"]
            cursor.withhold = kwargs["withhold"]
            if execute_error is not None:
                cursor.execute.side_effect = execute_error
            server_cursors.append(cursor)
            return cursor

        connection.cursor.side_effect = cursor_factory
        adapter.set_connection(connection)
        adapter.driver_io = Driver(adapter)
        self.addCleanup(adapter._clean_tlocals)
        return adapter, ordinary_cursor, server_cursors, cursor_calls

    def test_named_cursor_is_unique_holdable_and_uses_configured_batch_size(self):
        adapter, ordinary_cursor, cursors, cursor_calls = self._adapter(
            {"iterselect_fetch_size": 17}
        )
        events = []

        class RecordingHandler(ExecutionHandler):
            def before_execute(self, command):
                events.append(("before", command))

            def after_execute(self, command):
                events.append(("after", command))

        adapter.execution_handlers = [RecordingHandler]
        command = ParamSQL("SELECT %s", ("bound",))

        first = adapter._iterselect_cursor(command)
        second = adapter._iterselect_cursor("SELECT 2")

        self.assertNotEqual(first.name, second.name)
        self.assertTrue(first.name.startswith("pydal_iterselect_"))
        self.assertEqual(first.itersize, 17)
        self.assertEqual(second.itersize, 17)
        self.assertTrue(all(call["withhold"] for call in cursor_calls))
        cursors[0].execute.assert_called_once_with(command, ("bound",))
        ordinary_cursor.execute.assert_not_called()
        self.assertEqual(events[:2], [("before", command), ("after", command)])

    def test_fetch_size_must_be_a_positive_integer(self):
        adapter, _, cursors, _ = self._adapter()

        for value in (0, -1, True, False, 1.5, "10", None):
            with self.subTest(value=value):
                adapter.adapter_args["iterselect_fetch_size"] = value
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    adapter._iterselect_cursor("SELECT 1")

        self.assertEqual(cursors, [])

    def test_cursor_is_closed_when_execute_fails(self):
        adapter, _, cursors, _ = self._adapter(execute_error=RuntimeError("boom"))

        with self.assertRaisesRegex(RuntimeError, "boom"):
            adapter._iterselect_cursor("SELECT 1")

        cursors[0].close.assert_called_once_with()


class TestIterRowsLifecycle(unittest.TestCase):
    def _rows(self, fetch_results):
        db = DAL("sqlite:memory")
        db.define_table("t", Field("name"))
        self.addCleanup(db.close)
        adapter = db._adapter
        original = adapter._iterselect_cursor
        cursor = MagicMock(name="iterselect_cursor")
        cursor.fetchone.side_effect = fetch_results
        adapter._iterselect_cursor = lambda sql: cursor
        self.addCleanup(setattr, adapter, "_iterselect_cursor", original)
        return db(db.t).iterselect(orderby=db.t.id), cursor

    def test_exhaustion_closes_cursor_once(self):
        rows, cursor = self._rows([(1, "one"), (2, "two"), None])

        self.assertEqual([row.name for row in rows], ["one", "two"])
        cursor.close.assert_called_once_with()
        with self.assertRaises(StopIteration):
            next(rows)
        cursor.close.assert_called_once_with()

    def test_explicit_close_is_idempotent(self):
        rows, cursor = self._rows([(1, "one")])

        rows.close()
        rows.close()

        cursor.close.assert_called_once_with()
        with self.assertRaises(StopIteration):
            next(rows)

    def test_context_manager_closes_early_iteration(self):
        rows, cursor = self._rows([(1, "one"), (2, "two")])

        with rows as opened:
            self.assertIs(opened, rows)
            self.assertEqual(next(opened).name, "one")

        cursor.close.assert_called_once_with()

    def test_fetch_error_closes_cursor(self):
        rows, cursor = self._rows(RuntimeError("fetch failed"))

        with self.assertRaisesRegex(RuntimeError, "fetch failed"):
            next(rows)

        cursor.close.assert_called_once_with()