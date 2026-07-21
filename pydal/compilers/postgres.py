"""
PostgresCompiler: Postgres-specific expression compilation.

Overrides LIKE/ILIKE rendering to cast non-text operands to ``::text``
before comparison, since Postgres has no implicit integer→text coercion
for the ``~~`` (LIKE) operator.
"""

from __future__ import annotations

from .. import ast
from ..backends.postgres import Postgres, PostgresDialectArrays
from . import compilers
from .sql import SQLCompiler

_TEXT_TYPES = frozenset(("string", "text", "json", "jsonb"))


@compilers.register_for(Postgres)
class PostgresCompiler(SQLCompiler):
    def _render_like_left(self, l: ast.Node, lowered_left: bool) -> str:
        # For non-text fields (e.g. integer) Postgres rejects bare LIKE;
        # cast the operand to text first.
        rendered = self.visit(l)
        if getattr(l, "type", None) not in _TEXT_TYPES:
            rendered = "%s::text" % rendered
        return ("LOWER(%s)" % rendered) if lowered_left else rendered

    def op_contains(self, l, r, opts):
        ltype = opts.get("left_type") or self._left_type(l)
        dialect = getattr(self.adapter, "dialect", None)
        if (
            not isinstance(dialect, PostgresDialectArrays)
            or not ltype
            or not ltype.startswith("list:")
        ):
            return super(PostgresCompiler, self).op_contains(l, r, opts)

        case_sensitive = opts.get("case_sensitive", False)
        value_type = "string" if ltype == "list:string" else "integer"
        if case_sensitive and isinstance(r, ast.Literal):
            array_type = "TEXT" if ltype == "list:string" else "BIGINT"
            return "(%s @> ARRAY[%s]::%s[])" % (
                self.visit(l),
                self.visit(ast.Literal(r.value, value_type)),
                array_type,
            )
        rendered_right = self.visit(
            ast.Literal(r.value, value_type) if isinstance(r, ast.Literal) else r
        )
        if ltype == "list:string" and not isinstance(r, ast.Literal):
            rendered_right = "%s::text" % rendered_right
        rendered_left = self.visit(l)
        if not case_sensitive and ltype == "list:string":
            return "(%s ILIKE ANY(%s))" % (rendered_right, rendered_left)
        return "(%s = ANY(%s))" % (rendered_right, rendered_left)


__all__ = ["PostgresCompiler"]
