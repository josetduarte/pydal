"""
PostgresCompiler: Postgres-specific expression compilation.

Overrides LIKE/ILIKE rendering to cast non-text operands to ``::text``
before comparison, since Postgres has no implicit integer→text coercion
for the ``~~`` (LIKE) operator.
"""

from __future__ import annotations

from base64 import b64encode

from .. import ast
from ..backends.postgres import (
    Postgres,
    PostgresDialectArrays,
    PostgresPsyco,
)
from ..helpers.methods import bar_encode
from ..helpers.serializers import serializers
from ..utils import to_bytes
from . import compilers
from .sql import SQLCompiler

_TEXT_TYPES = frozenset(("string", "text", "json", "jsonb"))


@compilers.register_for(Postgres)
class PostgresCompiler(SQLCompiler):
    def _render_insert(self, n: ast.Insert, table: str, cols: str, values: str) -> str:
        sql = super()._render_insert(n, table, cols, values)
        if self.adapter is None:
            return sql
        table = self.adapter.db.get(n.table)
        if table is None or not hasattr(table, "_id"):
            return sql
        return "%s RETURNING %s;" % (sql.rstrip(";"), table._id._rname)

    def _render_like_left(self, l: ast.Node, lowered_left: bool) -> str:
        # For non-text fields (e.g. integer) Postgres rejects bare LIKE;
        # cast the operand to text first.
        rendered = self.visit(l)
        if getattr(l, "type", None) not in _TEXT_TYPES:
            rendered = "%s::text" % rendered
        return ("LOWER(%s)" % rendered) if lowered_left else rendered


@compilers.register_for(PostgresPsyco)
class PostgresPsycoCompiler(PostgresCompiler):
    parameterize = True
    placeholder_style = "format"

    @staticmethod
    def _ensure_list(value):
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    @staticmethod
    def _geo_srid(field_type):
        params = field_type[:-1].split("(", 1)[1].split(",")
        return params[1] if len(params) >= 2 else 4326

    def _bind(self, value, suffix=""):
        return "%s%s" % (self._ctx.bind(value), suffix)

    def v_Literal(self, n: ast.Literal) -> str:
        if self._ctx is None or n.value is None:
            return super().v_Literal(n)

        field_type = n.type
        if field_type in ("json", "jsonb"):
            return self._bind(serializers.json(n.value), "::%s" % field_type)

        if isinstance(field_type, str) and field_type.startswith("list:"):
            if n.value == "":
                return super().v_Literal(n)
            values = self._ensure_list(n.value)
            if field_type == "list:string":
                values = [str(value) for value in values]
                array_type = "text[]"
            else:
                values = [int(value) for value in values if value != ""]
                array_type = "bigint[]"
            if isinstance(getattr(self.adapter, "dialect", None), PostgresDialectArrays):
                return self._bind(values, "::%s" % array_type)
            return self._bind(bar_encode(values), "::text")

        if field_type == "blob":
            if n.value == "":
                return super().v_Literal(n)
            return self._bind(b64encode(to_bytes(n.value)), "::bytea")

        if field_type == "upload":
            return self._bind(str(n.value))

        if isinstance(field_type, str) and field_type.startswith("geometry("):
            if n.value == "":
                return super().v_Literal(n)
            if str(n.value).startswith("0"):
                return self._bind(str(n.value), "::geometry")
            return "ST_GeomFromText(%s,%s)" % (
                self._ctx.bind(str(n.value)),
                self._geo_srid(field_type),
            )

        if isinstance(field_type, str) and field_type.startswith("geography("):
            if n.value == "":
                return super().v_Literal(n)
            value = "SRID=%s;%s" % (self._geo_srid(field_type), n.value)
            return "ST_GeogFromText(%s)" % self._ctx.bind(value)

        return super().v_Literal(n)


__all__ = ["PostgresCompiler", "PostgresPsycoCompiler"]
