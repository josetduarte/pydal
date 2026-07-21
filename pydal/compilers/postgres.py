"""
PostgreSQL expression compilation and psycopg2 parameter adaptation.

Includes PostgreSQL JSON/GIS operators and bound representations for complex
values such as JSON, arrays, binary data, and PostGIS geometry/geography.
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

    def op_regexp(self, l, r, _):
        return "(%s ~ %s)" % (self.visit(l), self.visit(r))

    def op_json_key(self, l, r, _):
        return "%s->%s" % (self.visit(l), self.visit(r))

    def op_json_key_value(self, l, r, _):
        return "%s->>%s" % (self.visit(l), self.visit(r))

    def op_json_path(self, l, r, _):
        return "%s#>%s::text[]" % (self.visit(l), self.visit(r))

    def op_json_path_value(self, l, r, _):
        return "%s#>>%s::text[]" % (self.visit(l), self.visit(r))

    def op_json_contains(self, l, r, _):
        return "%s::jsonb@>%s::jsonb" % (self.visit(l), self.visit(r))

    def un_st_astext(self, x, _):
        return "ST_AsText(%s)" % self.visit(x)

    def un_st_aswkb(self, x, _):
        return self.visit(x)

    def un_st_x(self, x, _):
        return "ST_X(%s)" % self.visit(x)

    def un_st_y(self, x, _):
        return "ST_Y(%s)" % self.visit(x)

    def _st_binary(self, name, l, r):
        return "%s(%s,%s)" % (name, self.visit(l), self.visit(r))

    def op_st_contains(self, l, r, _):
        return self._st_binary("ST_Contains", l, r)

    def op_st_distance(self, l, r, _):
        return self._st_binary("ST_Distance", l, r)

    def op_st_equals(self, l, r, _):
        return self._st_binary("ST_Equals", l, r)

    def op_st_intersects(self, l, r, _):
        return self._st_binary("ST_Intersects", l, r)

    def op_st_overlaps(self, l, r, _):
        return self._st_binary("ST_Overlaps", l, r)

    def op_st_simplify(self, l, r, _):
        return self._st_binary("ST_Simplify", l, r)

    def op_st_simplifypreservetopology(self, l, r, _):
        return self._st_binary("ST_SimplifyPreserveTopology", l, r)

    def op_st_touches(self, l, r, _):
        return self._st_binary("ST_Touches", l, r)

    def op_st_within(self, l, r, _):
        return self._st_binary("ST_Within", l, r)

    def op_st_transform(self, l, r, _):
        return self._st_binary("ST_Transform", l, r)

    def fn_st_dwithin(self, args, _):
        return "ST_DWithin(%s,%s,%s)" % tuple(self.visit(arg) for arg in args)

    def fn_st_asgeojson(self, args, opts):
        return "ST_AsGeoJSON(%s,%s,%s)" % (
            self.visit(args[0]), opts["precision"], opts["options"]
        )


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

    def op_contains(self, l, r, opts):
        ltype = opts.get("left_type") or self._left_type(l)
        if not (
            isinstance(getattr(self.adapter, "dialect", None), PostgresDialectArrays)
            and ltype
            and ltype.startswith("list:")
        ):
            return super().op_contains(l, r, opts)

        value_type = "string" if ltype == "list:string" else "integer"
        if isinstance(r, ast.Literal):
            right = self.visit(ast.Literal(r.value, value_type))
        else:
            right = self.visit(r)
            if ltype == "list:string":
                right = "%s::text" % right
        left = self.visit(l)
        if not opts.get("case_sensitive", False) and ltype == "list:string":
            return "(%s ILIKE ANY(%s))" % (right, left)
        return "(%s = ANY(%s))" % (right, left)


__all__ = ["PostgresCompiler", "PostgresPsycoCompiler"]
