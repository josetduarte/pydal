# -*- coding: utf-8 -*-

"""
Tier-2 unit coverage: tiny utility modules that didn't previously
have dedicated tests.

* ``pydal.exceptions``
* ``pydal.utils`` (to_bytes / to_native / to_unicode)
* ``pydal._globals`` (IDENTITY / OR / AND / DEFAULT)
* ``pydal._load`` (OrderedDict / portalocker imports)
* ``pydal.utils`` (utcnow / deprecated / split_uri_args)
* ``pydal.drivers`` (DRIVERS dict + sentinel attrs)
* ``pydal.connection.ConnectionPool``
* ``pydal.helpers._internals.Dispatcher``
"""

import datetime
import threading
import warnings

from pydal import _globals, _load, drivers, exceptions, utils
from pydal.connection import ConnectionPool
from pydal.helpers._internals import Dispatcher

from ._compat import unittest


class TestExceptions(unittest.TestCase):

    def test_not_found_is_an_exception(self):
        with self.assertRaises(exceptions.NotFoundException):
            raise exceptions.NotFoundException("x")

    def test_not_authorized_is_an_exception(self):
        with self.assertRaises(exceptions.NotAuthorizedException):
            raise exceptions.NotAuthorizedException("x")

    def test_not_on_nosql_has_default_message(self):
        try:
            raise exceptions.NotOnNOSQLError()
        except exceptions.NotOnNOSQLError as e:
            self.assertIn("NoSQL", str(e))

    def test_not_on_nosql_accepts_custom_message(self):
        try:
            raise exceptions.NotOnNOSQLError("custom")
        except exceptions.NotOnNOSQLError as e:
            self.assertEqual(str(e), "custom")

    def test_not_on_nosql_is_notimplementederror(self):
        # Code that catches NotImplementedError should still trip on this.
        with self.assertRaises(NotImplementedError):
            raise exceptions.NotOnNOSQLError()


class TestUtilsCoercion(unittest.TestCase):

    def test_to_bytes_none_passthrough(self):
        self.assertIsNone(utils.to_bytes(None))

    def test_to_bytes_str(self):
        self.assertEqual(utils.to_bytes("foo"), b"foo")

    def test_to_bytes_bytes(self):
        self.assertEqual(utils.to_bytes(b"foo"), b"foo")

    def test_to_bytes_bytearray(self):
        self.assertEqual(utils.to_bytes(bytearray(b"foo")), b"foo")

    def test_to_bytes_rejects_non_text(self):
        with self.assertRaises(TypeError):
            utils.to_bytes(123)

    def test_to_native_passthrough(self):
        self.assertEqual(utils.to_native("foo"), "foo")
        self.assertEqual(utils.to_native(b"foo"), "foo")
        self.assertIsNone(utils.to_native(None))

    def test_to_unicode_passthrough(self):
        self.assertEqual(utils.to_unicode("foo"), "foo")
        self.assertEqual(utils.to_unicode(b"foo"), "foo")
        self.assertIsNone(utils.to_unicode(None))


class TestGlobals(unittest.TestCase):

    def test_identity(self):
        self.assertEqual(_globals.IDENTITY(42), 42)
        self.assertEqual(_globals.IDENTITY("x"), "x")

    def test_or_and_operators_on_ints(self):
        # OR/AND just defer to operators — exercise the contract.
        self.assertEqual(_globals.OR(0b10, 0b01), 0b11)
        self.assertEqual(_globals.AND(0b11, 0b01), 0b01)

    def test_default_sentinel_callable_returns_none(self):
        self.assertIsNone(_globals.DEFAULT())


class TestLoad(unittest.TestCase):

    def test_ordereddict_is_stdlib(self):
        import collections
        self.assertIs(_load.OrderedDict, collections.OrderedDict)

    def test_portalocker_importable(self):
        # Just confirm the symbol exists; portalocker has many APIs.
        self.assertIsNotNone(_load.portalocker)


class TestUtils(unittest.TestCase):

    def test_utcnow_returns_naive_datetime(self):
        now = utils.utcnow()
        self.assertIsInstance(now, datetime.datetime)
        self.assertIsNone(now.tzinfo)
        # within 5s of system UTC clock
        ref = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self.assertLess(abs((ref - now).total_seconds()), 5)

    def test_deprecated_decorator_warns_and_returns(self):
        @utils.deprecated("old", "new", "X")
        def fn(x):
            return x * 2

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", utils.RemovedInNextVersionWarning)
            result = fn(3)
        self.assertEqual(result, 6)
        self.assertTrue(any(
            issubclass(w.category, utils.RemovedInNextVersionWarning)
            for w in caught
        ))

    def test_split_uri_args_basic(self):
        got = utils.split_uri_args("a=1&b=2")
        self.assertEqual(got, {"a": "1", "b": "2"})

    def test_split_uri_args_bare_key(self):
        # With need_equal=False (default), bare keys are accepted
        # with value None.
        got = utils.split_uri_args("flag&a=1")
        self.assertIn("flag", got)
        self.assertEqual(got["a"], "1")

    def test_split_uri_args_need_equal_form(self):
        # need_equal=True only matches key=value pairs.
        got = utils.split_uri_args("a=1&b=2", need_equal=True)
        self.assertEqual(got, {"a": "1", "b": "2"})


class TestDrivers(unittest.TestCase):

    def test_drivers_is_a_dict(self):
        self.assertIsInstance(drivers.DRIVERS, dict)

    def test_sqlite3_is_always_present(self):
        # sqlite3 ships with cpython, so it must be in DRIVERS.
        self.assertIn("sqlite3", drivers.DRIVERS)

    def test_sentinel_attributes_defined_regardless_of_install(self):
        # These attributes MUST exist whether or not their driver was
        # installed — the regression that prompted the fix would have
        # them undefined when firestore is missing.
        for name in ("psycopg2_adapt", "cx_Oracle", "pyodbc", "couchdb"):
            self.assertTrue(
                hasattr(drivers, name),
                "drivers.%s is unbound" % name,
            )
        self.assertIsInstance(drivers.is_jdbc, bool)


class TestConnectionPool(unittest.TestCase):

    def test_rejected_pooled_connection_is_closed_and_unbound(self):
        class Cursor:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class Connection:
            def __init__(self, broken=False):
                self.broken = broken
                self.closed = False
                self.cursor_instance = Cursor()

            def cursor(self):
                return self.cursor_instance

            def close(self):
                self.closed = True

        class Pool(ConnectionPool):
            pool_size = 2

            def connector(self):
                raise AssertionError("pooled connection was not reused")

            def test_connection(self):
                if self.connection.broken:
                    raise RuntimeError("broken connection")

        pool = Pool()
        pool.uri = "pool-rejection-test-%s" % id(self)
        good = Connection()
        broken = Connection(broken=True)
        ConnectionPool.POOLS[pool.uri] = [good, broken]
        try:
            connection = pool.get_connection()
            self.assertIs(connection, good)
            self.assertIs(pool.connection, good)
            self.assertIs(pool.cursor, good.cursor_instance)
            self.assertTrue(broken.cursor_instance.closed)
            self.assertTrue(broken.closed)
        finally:
            pool.set_connection(None)
            ConnectionPool.POOLS.pop(pool.uri, None)

    def test_concurrent_checkouts_validate_outside_global_lock(self):
        state_lock = threading.Lock()
        validation_barrier = threading.Barrier(2, timeout=5)
        active_validations = [0]
        max_active_validations = [0]
        errors = []
        uri = "pool-lock-test-%s" % id(self)

        class Cursor:
            def close(self):
                pass

        class Connection:
            def cursor(self):
                return Cursor()

            def close(self):
                pass

        class Pool(ConnectionPool):
            pool_size = 2

            def connector(self):
                raise AssertionError("pooled connection was not reused")

            def test_connection(self):
                with state_lock:
                    active_validations[0] += 1
                    max_active_validations[0] = max(
                        max_active_validations[0], active_validations[0]
                    )
                try:
                    validation_barrier.wait()
                finally:
                    with state_lock:
                        active_validations[0] -= 1

        pool = Pool()
        pool.uri = uri
        ConnectionPool.POOLS[uri] = [Connection(), Connection()]

        def checkout():
            try:
                pool.get_connection()
            except Exception as error:
                with state_lock:
                    errors.append(error)

        threads = [threading.Thread(target=checkout) for _ in range(2)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
        finally:
            ConnectionPool.POOLS.pop(uri, None)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(max_active_validations[0], 2)


class TestDispatcher(unittest.TestCase):

    def test_register_and_get(self):
        d = Dispatcher("widget")

        class Base:
            pass

        class Wrap:
            def __init__(self, obj):
                self.obj = obj

        d.register_for(Base)(Wrap)
        b = Base()
        rv = d.get_for(b)
        self.assertIsInstance(rv, Wrap)
        self.assertIs(rv.obj, b)

    def test_get_walks_mro(self):
        d = Dispatcher("widget")

        class Base:
            pass

        class Child(Base):
            pass

        class Wrap:
            def __init__(self, obj):
                self.obj = obj

        d.register_for(Base)(Wrap)
        rv = d.get_for(Child())
        self.assertIsInstance(rv, Wrap)

    def test_unregistered_raises(self):
        d = Dispatcher("widget")
        with self.assertRaises(ValueError):
            d.get_for(object())
