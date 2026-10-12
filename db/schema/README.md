# Database schema

Telescreen's DDL is designed in the [Diagra](https://github.com/tacogips/diagra-app)
desktop app. `telescreen.diagra.jsonl` is the source of truth; everything else
in this directory is generated from it.

| File | Role |
| --- | --- |
| `telescreen.diagra.jsonl` | Diagra ERD document (page "Telescreen schema"), edited only in the Diagra app |
| `telescreen.postgresql.sql` | PostgreSQL DDL exported by Diagra (`mise run ddl-export`) |
| `telescreen.sqlite.sql` | SQLite DDL exported by Diagra (`mise run ddl-export`) |

The SQLAlchemy models (`src/telescreen/db/models`) and the Alembic revisions
(`src/telescreen/db/migrations/versions`) still create and upgrade real
databases. `tests/db/test_diagra_schema.py` fails whenever they disagree with
the Diagra document, so a schema change that bypasses Diagra cannot pass CI.

## Setup

Install the Diagra app and its `diagra-cli` command (managed by
`mise-darwin`'s `Brewfile.desktop`):

```sh
brew install --cask tacogips/tap/diagra
```

## Changing the schema

1. `mise run ddl-edit` opens the document in Diagra. Add or change tables,
   columns, types, nullability, defaults, unique keys and indexes there, and
   draw a relation for every new reference column (see below). Arrange >
   Tidy layout (Cmd/Ctrl+Option+L) re-flows the tables and frames without
   overlaps; `mise run ddl-arrange` does the same headlessly. Save.
   Diagra's inspector shows the resulting DDL under Database DDL.
2. `mise run ddl-export` canonicalizes the document and regenerates both
   `.sql` exports. Review the SQL diff.
3. Update the SQLAlchemy models to match and add an Alembic revision that
   migrates existing databases.
4. `mise run ddl-check` verifies the document is canonical, the exports are
   current and the models match the design. `mise run ci` runs the same model
   and export checks through pytest (the `diagra-cli` freshness checks are
   skipped when the CLI is not installed).

Conventions in the document:

- Types use Diagra's portable spellings: `varchar(n)`, `text`, `integer`,
  `boolean`, `double precision`, `timestamp with time zone`.
- Every index carries the exact name the models use (`ix_<table>_<column>` or
  the explicit `Index` name). Unique constraints are Diagra unique indexes;
  leave the name empty for unnamed SQLAlchemy `unique=True` columns and set it
  for named `UniqueConstraint`s.
- Defaults are SQL literals (`0`, `'medium'`, `false`).
- Tables are grouped into frames by model module.
- References are drawn field to field. Telescreen enforces no foreign keys,
  so every relation is a *logical reference* (inspector: Constraint > Logical
  reference): drawn dashed from the referencing column to the referenced key,
  with no constraint in the DDL exports. The drift check requires matching
  column types and a primary or unique key on the referenced side. Polymorphic
  columns (`engine_tasks.ref_id`, `audit_records.target_id`) and textual actor
  references (`*_ref`) have no single target table and are not drawn. An
  enforced relation (logical unchecked) would become a real foreign key in the
  exports and must then exist in the models.

Not expressible in Diagra and therefore kept in Alembic code: the PostgreSQL
`audit_records` append-only trigger and the seed rows (`counters`,
`audit_chain_head`). Diagra emits unique keys as `CREATE UNIQUE INDEX` and an
identity column for `audit_chain_head.id`; Alembic creates the equivalent
unique constraints and `SERIAL`.
