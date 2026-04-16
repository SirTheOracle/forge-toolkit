# Evidence Manifest Schema

This schema defines the `manifest.yaml` file produced by the adversarial-qa skill and consumed by the adversarial-verify skill. Both skills reference this document to stay in sync.

## Full Schema

```yaml
version: "1.0"
slug: "{issue-slug}"
created: "{ISO 8601 timestamp}"
input_mode: "adversarial-artifacts | git-diff | standalone"

# Upstream artifacts that informed the QA scope (if available)
source_artifacts:
  final_plan: ".dev/proposals/{slug}/final-plan.md"           # from adversarial-proposal
  implementation: ".dev/proposals/{slug}/implementation.md"    # from adversarial-implementation

# Standard viewport sizes for reproducible screenshots
viewports:
  desktop: { width: 1280, height: 720 }
  mobile: { width: 375, height: 812 }

# Issues found during QA -- things that are broken
findings:
  - id: "F-001"                          # Sequential ID, unique within manifest
    title: "Short description of the issue"
    severity: "CRITICAL | HIGH | MEDIUM | LOW | INFO"
    status: "open"                       # open | verified-fixed | still-open | dismissed
    category: "ui-regression | api-contract | data-flow | feature-bug | console-error | performance"
    affected_area: "/gallery"            # URL path or component/stage identifier
    reproducible: "always | intermittent | once"
    found_by: "tester-a | tester-b | both | cross-verification"
    repro_steps:
      - "Navigate to /gallery"
      - "Click any video card"
      - "Observe lightbox on mobile viewport"
    expected: "Full video visible without cropping"
    actual: "Bottom 20% of video cut off"
    evidence:
      - type: "screenshot"
        path: "evidence/screenshots/F-001-gallery-lightbox-mobile.png"
        viewport: "mobile"              # which viewport was used
        url: "{{frontend_url}}/gallery"
        description: "Video cropped at 375px viewport"
      - type: "api-response"
        path: "evidence/api-responses/F-001-response.json"
        method: "GET"
        url: "/api/stages/6/audio?strategy_id=999"
        status_code: 500
        description: "Server error on empty audio list"
      - type: "test-output"
        path: "evidence/test-results/F-001-output.txt"
        command: "pytest tests/unit/test_audio.py::test_empty_list -v"
        exit_code: 1
        description: "Unit test failure"
      - type: "console-error"
        path: "evidence/test-results/F-001-console.txt"
        url: "{{frontend_url}}/gallery"
        description: "Uncaught TypeError in lightbox component"

# Passing checks -- things confirmed working (verification re-runs these for regression)
checks:
  - id: "C-001"                          # Sequential ID, unique within manifest
    description: "Dashboard loads without console errors"
    status: "pass"                       # pass | fail | flaky
    category: "ui-regression | api-contract | data-flow | feature-check"
    affected_area: "/dashboard"
    evidence:
      - type: "screenshot"
        path: "evidence/screenshots/C-001-dashboard-load.png"
        viewport: "desktop"
        url: "{{frontend_url}}/dashboard"
        description: "Dashboard renders correctly"
      - type: "command"
        command: "npx playwright screenshot --full-page --viewport-size=1280,720 {{frontend_url}}/dashboard evidence/screenshots/C-001-dashboard-load.png"
        exit_code: 0
```

## Evidence Types

| Type | Required Fields | Description |
|------|----------------|-------------|
| `screenshot` | `path`, `viewport`, `url` | Playwright screenshot at specific viewport/URL |
| `api-response` | `path`, `method`, `url`, `status_code` | HTTP response saved as JSON |
| `test-output` | `path`, `command`, `exit_code` | stdout/stderr from a test command |
| `console-error` | `path`, `url` | Browser console errors captured during page load |
| `command` | `command`, `exit_code` | Generic command execution record |

## Evidence Directory Structure

```
.dev/qa/{slug}/
  manifest.yaml
  evidence/
    screenshots/
      F-001-gallery-lightbox-mobile.png
      C-001-dashboard-load.png
      C-002-stage1-load.png
    api-responses/
      F-002-stage6-audio-500.json
    test-results/
      F-001-playwright-output.txt
      C-005-pytest-results.txt
    verify-cycle-1/            # created by adversarial-verify
      screenshots/
        F-001-recheck.png
        C-001-recheck.png
      diffs/
        F-001-diff.png
        C-001-diff.png
    verify-cycle-2/            # if cycle 2 runs
      screenshots/
      diffs/
```

## Naming Conventions

- Finding evidence: `{finding-id}-{short-slug}.{ext}` (e.g., `F-001-gallery-lightbox-mobile.png`)
- Check evidence: `{check-id}-{short-slug}.{ext}` (e.g., `C-001-dashboard-load.png`)
- Verification re-checks: `{id}-recheck.png` in `verify-cycle-N/screenshots/`
- Pixel diffs: `{id}-diff.png` in `verify-cycle-N/diffs/`

## ID Allocation

- Findings: `F-001`, `F-002`, ... (sequential per tester, merged by synthesizer)
- Checks: `C-001`, `C-002`, ... (sequential per tester, merged by synthesizer)
- Tester A uses `F-A01`, `C-A01` in their per-tester manifest
- Tester B uses `F-B01`, `C-B01` in their per-tester manifest
- Synthesizer renumbers to `F-001`, `C-001` in the merged manifest

## Status Transitions

### Finding statuses
```
open → verified-fixed    (verify confirms the fix resolved it)
open → still-open        (verify confirms the issue persists)
open → dismissed         (synthesizer determined false positive)
```

### Check statuses
```
pass → still-passing     (verify confirms no regression)
pass → regressed         (verify finds it now fails)
```
