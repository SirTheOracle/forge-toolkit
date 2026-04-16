# Docs Refresh Manifest Format

The docs-refresh skill is global, but each project opts in with a local
manifest:

- preferred: `.codex/docs-refresh.yml`
- fallback: `.claude/docs-refresh.yml`

The manifest declares:

- source aliases
- target docs
- refreshable section IDs
- which inventories feed which sections

## Minimal Shape

```yaml
version: 1

sources:
  project.identity:
    - CLAUDE.md
    - .claude/forge-project.yml

targets:
  - id: product-guide
    path: docs/product-guide.md
    sections:
      - id: overview
        sources: [project.identity]
```

## Recommended Shape

```yaml
version: 1

sources:
  project.identity:
    - CLAUDE.md
    - .claude/forge-project.yml
  runtime.services:
    - docker-compose.yml
    - .claude/forge-project.yml
  backend.routes:
    - backend/src/app/main.py
    - backend/src/app/api/*.py
  backend.models:
    - backend/src/app/models/__init__.py
    - backend/src/app/models/*.py
  backend.enums:
    - backend/src/app/models/enums.py
  backend.migrations:
    - backend/alembic/versions/*
  frontend.routes:
    - frontend/src/App.tsx
  frontend.navigation:
    - frontend/src/config/appNavigation.ts
  frontend.shell:
    - frontend/src/components/Layout.tsx
  workflows.core:
    - .claude/forge-project.yml

targets:
  - id: product-guide
    path: docs/product-guide.md
    sections:
      - id: overview
        sources: [project.identity]
      - id: pipeline-walkthrough
        sources: [project.identity, backend.enums, workflows.core]
      - id: screen-map
        sources: [frontend.routes, frontend.navigation, frontend.shell]
      - id: glossary
        sources: [backend.enums, backend.models]

  - id: technical-reference
    path: docs/technical-reference.md
    sections:
      - id: architecture
        sources: [project.identity, runtime.services]
      - id: runtime-topology
        sources: [runtime.services]
      - id: api-endpoints
        sources: [backend.routes]
      - id: data-models-and-migrations
        sources: [backend.models, backend.enums, backend.migrations]
      - id: frontend-structure
        sources: [frontend.routes, frontend.navigation, frontend.shell]
      - id: dev-test-ops-commands
        sources: [workflows.core, project.identity]
      - id: conventions-and-pitfalls
        sources: [project.identity]
```

## Validation Rules

The skill should validate all of these before generation:

1. `version` exists.
2. Every `target.id` is unique.
3. Every target has a `path`.
4. Every section inside a target has a unique `id`.
5. Every section `sources` entry refers to a declared alias.
6. Every source path or glob resolves to at least one file.

## Alias Design Guidance

Design aliases around inventories, not around vague topics.

Good alias patterns:

- `backend.routes`
- `frontend.navigation`
- `runtime.services`
- `backend.migrations`

Weak alias patterns:

- `backend.everything`
- `frontend.all`
- `misc`

The skill should be able to answer two questions from the alias name alone:

1. What files will be read?
2. What facts are expected to come out of those files?

## Recommended Inventory Meanings

| Alias | Expected inventory |
|---|---|
| `project.identity` | project name, summary, ports, commands, conventions |
| `runtime.services` | service names, ports, dependencies, images, runtime roles |
| `backend.routes` | router groups, paths, methods, auth/admin requirements |
| `backend.models` | registered models, key columns, relationships, important table roles |
| `backend.enums` | statuses, enum values, lifecycle transitions |
| `backend.migrations` | version history and schema evolution clues |
| `frontend.routes` | route tree, detail pages, nesting |
| `frontend.navigation` | labels, ordering, access metadata, navigation structure |
| `frontend.shell` | badge behavior, sidebar logic, current-page naming, mobile shell behavior |
| `workflows.core` | local run/test/migration commands, smoke pages, core workflows |

## Source-Tier Reminder

The manifest does not change source authority. The skill should still prefer:

1. code/runtime surfaces
2. structured config/manifests
3. curated prose

The manifest declares what may be read, not what automatically wins every dispute.
