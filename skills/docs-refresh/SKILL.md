---
name: docs-refresh
description: >
  Generates and maintains living documentation from the current codebase state.
  Reads a project-local manifest (.claude/docs-refresh.yml) that declares doc
  targets, sections, source inventories, and refresh boundaries. Uses marker
  blocks in docs to safely regenerate content without trampling manual prose.
---

# Docs Refresh Skill

## Hard Constraints

1. Never auto-commit — write files only, let the user review via `git diff`
2. Never overwrite text outside marker blocks — manual prose is sacred
3. Never invent facts — every claim must trace to a source file you actually read
4. Never scan the entire codebase — only read files declared in the manifest's source inventories
5. Never silently resolve conflicts between sources — surface contradictions as visible TODOs
6. Always read the manifest before generating anything
7. Always preserve existing marker block boundaries — never merge, split, or relocate them

## Required Reads

Before generating anything, read these files in order:

1. `.claude/docs-refresh.yml` — the project manifest (required)
2. `CLAUDE.md` — project context (if exists)
3. Each source inventory file declared in the manifest

If `.claude/docs-refresh.yml` does not exist:
```
DOCS-REFRESH ERROR: No .claude/docs-refresh.yml found.
Create a manifest with doc targets and source inventories before running this skill.
See ~/.claude/skills/docs-refresh/SKILL.md for the manifest format.
```

## Manifest Format

The project-local manifest at `.claude/docs-refresh.yml` declares what to generate.

```yaml
version: 1

# Source inventory aliases — map logical names to file paths/ranges
sources:
  project.identity:
    - CLAUDE.md
    - .claude/forge-project.yml
  runtime.services:
    - docker-compose.yml
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

# Document targets
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
      - id: data-models
        sources: [backend.models, backend.enums, backend.migrations]
      - id: frontend-structure
        sources: [frontend.routes, frontend.navigation, frontend.shell]
      - id: dev-test-ops
        sources: [workflows.core, project.identity]
      - id: conventions
        sources: [project.identity]
```

## Invocation

```
/docs-refresh                           # refresh all targets
/docs-refresh technical-reference       # refresh one target
```

## Phase 0: Manifest Loading and Validation

1. Read `.claude/docs-refresh.yml`
2. Validate:
   - Every `target.id` is unique
   - Every `section.id` is unique within its target
   - Every source alias referenced by a section exists in the `sources` map
   - Every source file path exists on disk (glob patterns are resolved)
3. If validation fails, report clearly and stop:
   ```
   DOCS-REFRESH VALIDATION ERROR:
   - Section "api-endpoints" references unknown source "backend.controllers"
   - Source "frontend.routes" path "frontend/src/App.tsx" not found
   ```

## Phase 1: Build Source Inventories

For each source alias needed by the target(s) being refreshed:

1. Read the declared files
2. Extract structured facts into an inventory:

| Source Alias | What to Extract |
|---|---|
| `project.identity` | Project name, description, overview, port numbers |
| `runtime.services` | Service names, images, ports, dependencies, health checks |
| `backend.routes` | Router registrations from main.py, then for each route file: prefix, tags, auth dependencies, endpoint methods/paths |
| `backend.models` | Model class names from `__init__.py`, key columns and relationships from individual model files |
| `backend.enums` | Enum classes and their values (status transitions, pipeline phases) |
| `backend.migrations` | Migration filenames and descriptions (version history) |
| `frontend.routes` | Route paths, component mappings, parameterized routes, layout nesting |
| `frontend.navigation` | Nav items with key, label, segment, isIndex, adminOnly, showBadgePlaceholder |
| `frontend.shell` | Layout structure, sidebar behavior, badge rendering, mobile support |
| `workflows.core` | Test commands, dev commands, smoke pages, core workflows |

### Source Tier Policy

When sources disagree, prefer the higher tier:

1. **Code/runtime surfaces** — routers, models, route tree, compose services (highest authority)
2. **Structured config/manifests** — forge-project.yml, appNavigation.ts
3. **Curated prose** — CLAUDE.md, existing docs (lowest authority for facts)

If a lower-tier source contradicts a higher-tier source:
- Use the higher-tier fact in generated content
- Add a visible note: `<!-- TODO: CLAUDE.md says X but code shows Y — verify and update -->`

### Frontend Merge Rules

Frontend screen documentation requires merging three sources:

- `frontend.routes` (App.tsx) — authoritative for reachable routes and parameterized pages
- `frontend.navigation` (appNavigation.ts) — authoritative for nav labels, order, adminOnly
- `frontend.shell` (Layout.tsx) — augments with visibility behavior, badges, mobile

Hidden-but-reachable routes (in App.tsx but not in navigation) must be documented separately, not silently dropped.

## Phase 2: Generate Content

For each target being refreshed, for each section:

1. Check if the target doc file exists
2. If it exists, read it and locate the marker blocks
3. If it doesn't exist, create it with all section headers and empty marker blocks

### Marker Block Format

```md
## Section Heading

Optional manual framing text here — never touched by the skill.

<!-- docs-refresh:start section=section-id -->
...generated content goes here...
<!-- docs-refresh:end section=section-id -->

More manual text can follow — also never touched.
```

### Generation Rules

- Only write content inside marker blocks
- Everything outside markers is preserved exactly as-is
- Generated content should be clear, accurate, and sourced from the inventories built in Phase 1
- Use markdown tables for structured data (API endpoints, model summaries, service topology)
- Use prose for narrative sections (overview, architecture, conventions)
- Keep language appropriate to the target audience:
  - `product-guide`: non-technical, stakeholder-friendly, plain language
  - `technical-reference`: developer-oriented, specific, with code references

### Section Content Guidelines

#### Product Guide Sections

| Section | Content |
|---|---|
| `overview` | One-paragraph product summary, who built it, what it does |
| `pipeline-walkthrough` | The content pipeline phases explained in plain language with what happens at each step |
| `screen-map` | Every UI screen with what it does and its workflow context |
| `glossary` | Key terms with plain-language definitions |

#### Technical Reference Sections

| Section | Content |
|---|---|
| `architecture` | Backend/frontend/database architecture, how they connect, key design decisions |
| `runtime-topology` | Every service from docker-compose with ports, images, dependencies |
| `api-endpoints` | Table: method, path, auth requirement, description. Grouped by router |
| `data-models` | Table: model, key columns, relationships. Plus enum values and migration history summary |
| `frontend-structure` | Route tree, navigation manifest, component hierarchy, state management |
| `dev-test-ops` | Commands for running locally, testing, migrations, seeding, Docker |
| `conventions` | Key patterns: async SQLAlchemy, enum strategy, testing approach, import conventions |

## Phase 3: Write and Report

1. Write the updated doc files
2. Report what was done:
   ```
   DOCS-REFRESH COMPLETE
   Targets refreshed: product-guide, technical-reference
   Sections updated: 11
   Source files read: 24
   Conflicts found: 1 (see TODO comments in docs)

   Files written:
   - docs/product-guide.md
   - docs/technical-reference.md

   Review with: git diff docs/
   ```

## Marker Safety Rules

Before writing any file:

1. Verify every section declared in the manifest has a matching marker pair in the doc
2. If a marker is missing, add it (with the section heading) at the end of the document
3. If a marker is malformed (mismatched start/end, duplicate section IDs), stop and report:
   ```
   DOCS-REFRESH MARKER ERROR in docs/technical-reference.md:
   - Line 45: start marker for "api-endpoints" has no matching end marker
   - Line 89: duplicate start marker for "data-models"
   ```
4. Never create nested markers
5. Never move existing markers to a different position in the document

## Creating Docs From Scratch

When a target doc file does not exist yet:

1. Create the file with a title heading (`# Product Guide` or `# Technical Reference`)
2. For each section in the manifest (in order):
   - Add a `## Section Heading` (derive from section ID: `api-endpoints` -> `## API Endpoints`)
   - Add the marker block pair
   - Generate the initial content inside the markers
3. This produces a complete document with markers already in place

## Error Handling

| Error | Action |
|---|---|
| Manifest not found | Stop, report with setup instructions |
| Manifest validation fails | Stop, report all errors |
| Source file not found | Warn, skip that source, note in report |
| Marker error in existing doc | Stop, report the specific marker issue |
| Source tier conflict | Generate from higher tier, add TODO comment |
| Target directory doesn't exist | Create it (e.g., `mkdir -p docs/`) |

## What This Skill Does NOT Do

- Does not auto-commit (user reviews first)
- Does not scan files beyond what the manifest declares
- Does not edit CLAUDE.md or any source files
- Does not manage build-unit mapping
- Does not integrate with forge-coder or other pipeline skills (deferred)
- Does not generate changelogs or ADRs (separate concerns)

## Extending to New Documents

To add a new document target:

1. Add a new entry under `targets` in `.claude/docs-refresh.yml`
2. Define its sections and source mappings
3. Run `/docs-refresh` — the skill creates the new file with markers automatically

No skill code changes required. The manifest drives everything.
