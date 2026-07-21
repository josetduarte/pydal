# pydal — refactor TODO

The four edge cases that previously fell back to legacy are all now
handled by the AST pipeline. This file documents what was done and
what (small) corners remain.

## Resolved

### 1. Bare-table joins (`join=db.t2` / `left=db.t2`) — done

- `join=table` (bare) → `CROSS JOIN table`. Legacy pydal silently
  dropped the table; emitting CROSS JOIN is the standard interpretation
  and matches what users almost certainly intend.
- `left=table` (bare) → `LEFT JOIN table ON 1`. Legacy pydal emitted
  the invalid `LEFT JOIN t1,t2`; an unconstrained LEFT JOIN is the
  closest well-formed reading.
- AST: `Join("cross", target, None)` / `Join("left", target,
  Literal(True))`.

### 2. Writes on aliased tables — done

- `ast.Insert`/`Update`/`Delete` gained an `Optional[str] sqlsafe`
  field that the translator pre-bakes from `table._rname` (INSERT) or
  `dialect.writing_alias(table)` (UPDATE/DELETE).
- INSERT always targets the underlying physical table (mirroring
  `_insert` legacy behavior).
- UPDATE/DELETE on aliased tables raise `SyntaxError` at translation
  time on SQLite (the dialect's `writing_alias` rejects them) — same
  semantics as before, but the failure happens earlier.

### 3. `outer_scoped=...` attr — done

- `ast.Select.outer_scope: Tuple[str, ...]` carries the extra table
  names a caller wants treated as already-in-scope. Translator
  consumes `attrs["outer_scoped"]` and stores it on the node. The
  compiler's `_compile_select_body` unions it with the parent
  scope frozenset, so correlated-subquery pruning sees them too.

### 4. `correlated=False` — done

- `set_to_select` pops `correlated` from attrs before validation and
  applies it to the returned `ast.Select` (overriding the default
  `correlated=True`). Mirrors what `_select_to_ast` already did for
  legacy `Select` bridging.
- `set.subselect(..., correlated=False)` works directly now.

### 5. MSSQL `limitby` — done

- `SQLCompiler._emit_limitby(n, dst)` hook added; default emits ANSI
  `LIMIT`/`OFFSET`.
- `pydal/compilers/mssql.py` adds `MSSQLCompiler` (`mssql`, `mssqln`),
  `MSSQL3Compiler` (`mssql3`, `mssql3n`) and `MSSQL4Compiler`
  (`mssql4`, `mssql4n`), each overriding `_emit_limitby` to emit
  `TOP` / `OFFSET … FETCH NEXT` as appropriate.
- Removes the `self.compiler = None` workaround that previously forced
  all MSSQL queries through the legacy dialect path.

### 6. Postgres backend verified + `LIKE` on non-text fields — done

- The full suite now runs against PostgreSQL (Docker, CI service in
  `.github/workflows/run_test.yaml`) in addition to SQLite.
- `pydal/compilers/postgres.py` adds `PostgresCompiler`, overriding
  `_render_like_left` to cast non-text operands to `::text` — Postgres
  has no implicit `integer → text` coercion for the `~~` (LIKE)
  operator, so `field.like(...)` on an integer column needs the cast.
  The legacy `PostgresDialect.like` got the same `::text` fix (the old
  `CAST(x AS CHAR(n))` form was broken).
- `SQLCompiler._render_like_left` hook extracted in the base so the
  Postgres override is a one-liner.

### 7. PostgreSQL complex bound parameters — done

- psycopg2 now binds `json`/`jsonb`, serialized and native `list:*`,
  `blob`, `upload`, `geometry`, and `geography` values.
- Existing storage contracts are preserved: base `postgres` list fields remain
  pipe-delimited text, `postgres2`/`postgres3` use typed native arrays, blobs
  remain base64 payloads in `BYTEA`, and spatial values use parameterized
  PostGIS constructors with their configured SRID.
- JDBC PostgreSQL remains inline because its placeholder contract is separate
  from psycopg2's `%s` format.

### 8. PostgreSQL AST fallback removal — done

- psycopg2 PostgreSQL no longer silently falls back to the legacy statement
  builders. Unsupported AST shapes now fail loudly.
- The remaining exercised gaps are implemented: simultaneous inner/left joins,
  multi-table counts, expression-based `contains`, regexp, PostgreSQL JSON and
  spatial operators, CTE collector plumbing, and raw/custom select fields.
- SQLite and non-PostgreSQL adapters retain their existing fallback policy.

---

## Remaining corners (low-priority)

These are real but cheap-to-leave-alone:

- **Tests against MySQL / other backends.** SQLite and PostgreSQL are
  both verified (PostgreSQL runs in CI). Running the suite against
  MySQL would confirm the remaining dialect-specific behavior and catch
  any bit-rotted overrides.
- **Retire the legacy `_select_wcols` body globally.** psycopg2 PostgreSQL is
  strict-AST now, but other SQL adapters retain the legacy block for production
  shapes outside their test surfaces. Removing it globally needs equivalent
  strict validation for each backend.
- **NoSQL backends (`mongo`, `gae`, `couchdb`)** aren't wired through
  the AST. They have their own dialects/representers and the
  `_load_dependencies` compiler-lookup falls through to `None` for
  them. The AST is SQL-shaped; a parallel `NoSQLCompiler` would be a
  separate effort.

---

## Coverage snapshot

| Surface area | Status |
|---|---|
| Single-table SELECT/INSERT/UPDATE/DELETE/COUNT | ✅ AST |
| WHERE / GROUP BY / HAVING / ORDER BY / LIMIT | ✅ AST |
| Common filters | ✅ AST |
| DISTINCT bool, DISTINCT ON expr, DISTINCT ON list | ✅ AST |
| FOR UPDATE | ✅ AST |
| `join=` / `left=` with `.on()` and with bare tables | ✅ AST |
| Implicit multi-table cross-join | ✅ AST |
| Subqueries: `subselect`, `nested_select`, `_select` | ✅ AST |
| CTE (non-recursive + recursive) | ✅ AST |
| Select as a join source | ✅ AST |
| Aliased fields and tables | ✅ AST |
| Writes on aliased tables (INSERT/UPDATE/DELETE) | ✅ AST |
| `rname=` on tables and fields | ✅ AST |
| `correlated=False` on subselect | ✅ AST |
| `outer_scoped` attr | ✅ AST |
| Bound parameters: string/numeric/decimal | ✅ AST |
| Bound parameters: date/time/datetime/boolean | ✅ AST |
| PostgreSQL `list:*`, `json`, `blob`, `upload`, `geo*` literals | ✅ bound (psycopg2) |
| PostgreSQL statement fallback | disabled (psycopg2) |
| NoSQL backends | legacy (out of scope) |
