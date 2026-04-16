---
name: docs-refresh
description: >
  Manifest-driven documentation refresh skill. This skill should be used when
  the user asks to "refresh docs", "update docs from the codebase",
  "regenerate the technical reference", "sync the product guide", "maintain
  living documentation", or mentions `.codex/docs-refresh.yml` or
  `.claude/docs-refresh.yml`. Reads a project-local manifest, builds targeted
  source inventories, preserves manual prose via marker blocks, and refreshes
  documentation without auto-committing.
---

# Docs Refresh

## Overview

This skill provides a global Codex workflow for maintaining living project
documentation from a local project manifest. The global skill supplies the
refresh protocol; each project opts in by defining either:

- `.codex/docs-refresh.yml`, or
- `.claude/docs-refresh.yml`

This follows the same design principle as other global Codex skills that rely
on project-local configuration. The skill is reusable, but the source inventory,
target docs, and section boundaries remain project-specific.

The core operating model is fixed:

1. Validate the manifest and requested scope
2. Build only the inventories needed for the requested targets
3. Generate content from those inventories
4. Write only inside marker blocks

The skill is intentionally conservative. It does not scan the whole repo, does
not invent facts, does not silently reconcile conflicting sources, and does not
auto-commit changes.

## Hard Constraints

1. Never auto-commit. Write files only and leave review to the user.
2. Never overwrite text outside marker blocks. Manual prose is preserved as-is.
3. Never invent facts. Every generated claim must trace to a source file that was actually read.
4. Never scan the whole codebase by default. Read only files declared by the resolved manifest and only for the inventories needed by the current scope.
5. Never silently resolve source conflicts. Prefer the higher-tier source and surface the conflict as a visible note or TODO.
6. Always validate manifest structure before generating content.
7. Always validate marker safety before writing any existing document.
8. Never merge, split, reorder, or relocate existing marker blocks.

## Manifest Resolution

Resolve the project-local manifest before doing any other work:

1. If `.codex/docs-refresh.yml` exists, use it.
2. Otherwise, if `.claude/docs-refresh.yml` exists, use it.
3. If both exist, prefer `.codex/docs-refresh.yml` and note that the `.claude` manifest was ignored.
4. If neither exists, stop with a clear error:

```text
DOCS-REFRESH ERROR: No project manifest found.
Expected one of:
- .codex/docs-refresh.yml
- .claude/docs-refresh.yml

Create a local docs-refresh manifest before using this skill.
```

## Required Reads

Before generating anything, read these in order:

1. The resolved manifest file
2. `references/manifest-format.md`
3. `references/marker-blocks.md`
4. Only the source files needed by the requested targets and sections

Do not bulk-load every manifest source if the current request only touches one
target.

## Scope Resolution

Infer refresh scope from the user's request:

- If the user asks to refresh docs generally, refresh all targets in the manifest.
- If the user names a single target ID, path, or document, refresh only that target.
- If the user asks for a narrower scope than the manifest supports, stay within the manifest-defined targets and sections rather than guessing.

Keep the public behavior simple. This skill should support full refresh and
single-target refresh cleanly. Internal section IDs still matter, but the skill
should not pretend section-specific refresh exists unless the project's manifest
and docs are already structured for it.

## Phase 0: Validate Inputs

After resolving the manifest:

1. Parse the manifest.
2. Validate:
   - `version` exists
   - every `target.id` is unique
   - every `target.path` is present
   - every section in each target has a unique `id` within that target
   - every section references only declared source aliases
   - every declared source path or glob resolves to at least one file
3. Resolve requested scope against available targets.
4. Stop on validation failures. Report all failures together rather than one at a time.

Example:

```text
DOCS-REFRESH VALIDATION ERROR:
- Target "technical-reference" references unknown source alias "backend.controllers"
- Source alias "frontend.routes" resolved no files for pattern "frontend/src/App.tsx"
- Duplicate section id "overview" in target "product-guide"
```

## Phase 1: Build Source Inventories

For each source alias needed by the current scope:

1. Read the files declared for that alias.
2. Extract structured facts into an inventory.
3. Record enough structure to support deterministic regeneration.

Use inventories as the refresh unit, not build units and not raw "repo vibes".

Typical inventory categories:

| Alias | Purpose |
|---|---|
| `project.identity` | Project name, overview, commands, ports, conventions |
| `runtime.services` | Docker/runtime service topology, ports, dependencies |
| `backend.routes` | Router registration, prefixes, auth/admin dependencies, methods, paths |
| `backend.models` | Registered models, key tables, relationships, important columns |
| `backend.enums` | Enum names, status values, lifecycle transitions |
| `backend.migrations` | Migration filenames, version history, schema evolution clues |
| `frontend.routes` | Reachable routes, layout nesting, parameterized pages |
| `frontend.navigation` | Labels, order, segments, admin-only flags, badge placeholders |
| `frontend.shell` | Sidebar visibility, badge behavior, current-page naming, shell behavior |
| `workflows.core` | Dev/test/ops commands, smoke pages, key workflows |

### Source Tier Policy

When sources disagree, prefer the higher tier:

1. Code/runtime surfaces
2. Structured config/manifests
3. Curated prose and existing docs

If a lower-tier source contradicts a higher-tier source:

- use the higher-tier fact in generated content,
- preserve manual prose outside markers,
- surface the contradiction as a visible note or TODO inside the generated block,
- mention the conflict in the final refresh report.

Never smooth over a contradiction by rewriting around it as though it never existed.

### Frontend Merge Rules

Treat frontend documentation as a merge of three distinct inventories:

- `frontend.routes` is authoritative for reachable routes and parameterized detail pages.
- `frontend.navigation` is authoritative for nav labels, declared order, and access metadata such as `adminOnly`.
- `frontend.shell` augments visibility, badges, current-page naming, and shell-specific behavior.

Do not collapse these sources into one invented "screen list" without explaining
differences. Hidden-but-reachable routes and navigation-only distinctions should
be documented explicitly.

## Phase 2: Generate Content

For each target in scope:

1. If the target file does not exist, seed it with headings and marker blocks.
2. If the target file exists, read it and locate the marker blocks for each manifest section.
3. Generate only the content for the marker blocks.

Audience rules:

- `product-guide` style targets: plain language, stakeholder-friendly, minimal jargon
- `technical-reference` style targets: precise, implementation-oriented, concrete

Formatting rules:

- Use tables for structured inventories such as routes, services, models, and commands.
- Use prose for overviews, architecture, and conventions.
- Keep generated sections stable and deterministic enough that reruns without source changes produce empty or trivial diffs.

When creating a target from scratch, use the marker guidance in
`references/marker-blocks.md`.

## Phase 3: Write and Report

Before writing:

1. Validate marker safety for every existing target file.
2. Ensure target directories exist.
3. Replace only the contents inside each marker block.

After writing, report:

- manifest path used
- targets refreshed
- sections refreshed
- source aliases used
- source files read
- conflicts surfaced
- files written

Example:

```text
DOCS-REFRESH COMPLETE
Manifest: .claude/docs-refresh.yml
Targets refreshed: product-guide, technical-reference
Sections updated: 11
Source aliases used: project.identity, runtime.services, backend.routes, backend.models
Source files read: 18
Conflicts found: 1

Files written:
- docs/product-guide.md
- docs/technical-reference.md

Review changes with: git diff docs/
```

## Marker Safety Rules

Apply these rules before writing any existing target:

1. Every manifest-declared section must have exactly one matching marker pair in the file.
2. If a target file exists but a declared section is missing entirely, append the new section heading and marker pair at the end of the document. Do not relocate existing sections.
3. If marker structure is malformed, stop and report the issue.
4. Never create nested markers.
5. Never write outside the marker pair, even to "fix formatting".

Examples and exact marker syntax are in `references/marker-blocks.md`.

## Error Handling

Stop immediately on:

- missing manifest
- manifest validation failure
- malformed markers in an existing target
- ambiguous or duplicate marker IDs

Warn but continue only when the manifest and marker structure are valid and a
non-fatal source conflict is encountered. In that case, prefer the higher-tier
source and surface the conflict in the generated output and report.

## What This Skill Does Not Do

- Does not auto-commit
- Does not edit source files such as `CLAUDE.md`, `AGENTS.md`, code, or config
- Does not scan the whole repo outside manifest-declared inventories
- Does not use build units as the refresh key
- Does not silently rewrite manual prose
- Does not assume a single frontend file is the whole truth
- Does not integrate itself into build workflows automatically

## Additional Resources

Consult these bundled references as needed:

- `references/manifest-format.md` — manifest schema, example manifests, and alias guidance
- `references/marker-blocks.md` — marker syntax, scratch-file seeding, and marker safety examples

Keep the global skill reusable and keep project-specific rules in the local
manifest. That separation is the point of this design.
