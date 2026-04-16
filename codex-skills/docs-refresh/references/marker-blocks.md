# Marker Blocks and Target Seeding

Docs refresh preserves manual prose by only rewriting text inside marker blocks.

## Marker Format

Use this exact pattern:

```md
## Section Heading

Optional manual framing text here. This stays untouched.

<!-- docs-refresh:start section=section-id -->
Generated content goes here.
<!-- docs-refresh:end section=section-id -->
```

Everything outside the marker pair is manual prose and must remain unchanged.

## Scratch-File Seeding

When a target doc does not exist yet:

1. Create the file.
2. Add the top-level title:
   - `# Product Guide`
   - `# Technical Reference`
   - or another title derived from the target path/name
3. For each manifest-declared section, in order:
   - add a `##` heading derived from the section ID,
   - add the marker pair,
   - generate the initial section content inside the markers.

Example:

```md
# Technical Reference

## API Endpoints

<!-- docs-refresh:start section=api-endpoints -->
...generated endpoint table...
<!-- docs-refresh:end section=api-endpoints -->
```

## Heading Derivation

Default heading derivation is simple title casing:

- `api-endpoints` -> `## API Endpoints`
- `runtime-topology` -> `## Runtime Topology`
- `dev-test-ops-commands` -> `## Dev Test Ops Commands`

If a project wants different human-facing headings, keep the marker section ID
stable and edit the visible heading outside the marker block.

## Existing File Rules

When the target already exists:

1. Find exactly one start marker and one end marker for each manifest section.
2. Replace only the text between them.
3. Preserve surrounding whitespace and manual prose as much as possible.

If a manifest section is missing from an existing file:

1. Append the missing section at the end of the document.
2. Add its heading and marker pair there.
3. Do not move existing content to "make room" for it.

## Marker Errors

Stop and report on malformed structures such as:

- start marker without matching end marker
- end marker without matching start marker
- duplicate marker pair for the same section ID
- nested marker blocks

Example failure:

```text
DOCS-REFRESH MARKER ERROR in docs/technical-reference.md:
- start marker for "api-endpoints" has no matching end marker
- duplicate marker pair for "runtime-topology"
```

## Conflict Notes

When a lower-tier source contradicts a higher-tier source, keep the generated
fact aligned to the higher-tier source and surface the issue visibly inside the
generated block.

Example:

```md
<!-- TODO: CLAUDE.md says appNavigation.ts is the single source of truth for routes,
but App.tsx defines additional reachable detail routes. Verify project prose. -->
```

Do not insert such notes outside marker blocks unless the user explicitly wants
manual narrative changed.
