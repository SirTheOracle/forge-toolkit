# Verification Report Format

The adversarial-verify skill produces a `verification-report.yaml` after re-running all tests and comparing screenshots from the manifest.

## Schema

```yaml
version: "1.0"
slug: "{issue-slug}"
cycle: 1                                # 1 or 2
verified_at: "{ISO 8601 timestamp}"
source_manifest: ".dev/qa/{slug}/manifest.yaml"

verdict: "CLEAR | ISSUES_REMAIN"

# Summary counts
summary:
  total_findings: 3
  verified_fixed: 2
  still_open: 1
  total_checks: 15
  still_passing: 15
  regressed: 0

# Per-finding verification results
finding_results:
  - id: "F-001"
    title: "Gallery lightbox crops video on mobile"
    original_status: "open"
    verified_status: "verified-fixed | still-open"
    evidence:
      - type: "screenshot"
        path: "evidence/verify-cycle-1/screenshots/F-001-recheck.png"
        viewport: "mobile"
        url: "{{frontend_url}}/gallery"
      - type: "pixel-diff"
        baseline: "evidence/screenshots/F-001-gallery-lightbox-mobile.png"
        current: "evidence/verify-cycle-1/screenshots/F-001-recheck.png"
        diff: "evidence/verify-cycle-1/diffs/F-001-diff.png"
        mismatch_percentage: 1.5        # percentage of pixels that differ
        threshold: 5.0                   # percentage below which = "match"
      - type: "command"
        command: "npx playwright screenshot --viewport-size=375,812 {{frontend_url}}/gallery evidence/verify-cycle-1/screenshots/F-001-recheck.png"
        exit_code: 0
    notes: "Issue resolved -- video now shows full frame on mobile"

# Per-check regression results
check_results:
  - id: "C-001"
    description: "Dashboard loads without console errors"
    original_status: "pass"
    verified_status: "still-passing | regressed"
    evidence:
      - type: "screenshot"
        path: "evidence/verify-cycle-1/screenshots/C-001-recheck.png"
        viewport: "desktop"
        url: "{{frontend_url}}/dashboard"
      - type: "pixel-diff"
        baseline: "evidence/screenshots/C-001-dashboard-load.png"
        current: "evidence/verify-cycle-1/screenshots/C-001-recheck.png"
        diff: "evidence/verify-cycle-1/diffs/C-001-diff.png"
        mismatch_percentage: 0.2
        threshold: 5.0
    notes: "Dashboard still renders correctly"
```

## Verdict Logic

```
IF any finding has verified_status == "still-open"
   OR any check has verified_status == "regressed":
   verdict = "ISSUES_REMAIN"
ELSE:
   verdict = "CLEAR"
```

## Pixel-Diff Evidence

Each screenshot comparison produces three files:
- **baseline**: The original screenshot from QA (in `evidence/screenshots/`)
- **current**: The new screenshot taken during verification (in `evidence/verify-cycle-N/screenshots/`)
- **diff**: A visual diff image highlighting changed pixels (in `evidence/verify-cycle-N/diffs/`)

### Match Threshold

- `mismatch_percentage` < `threshold` (default 5.0%) = screenshots match
- Anti-aliasing and minor rendering differences are expected; the threshold accounts for this
- The threshold can be adjusted per-check if needed (e.g., stricter for pixel-perfect components)

## How Verification Handles Each Item

### For Findings (status: open)

1. Read the finding's `repro_steps` and `evidence` from the manifest
2. Re-run each evidence command (screenshot, curl, test)
3. For screenshot evidence: take new screenshot at same URL + viewport, run pixel-diff against original
4. For test evidence: re-run the exact command, compare exit code
5. For API evidence: re-curl the endpoint, compare status code and response shape
6. If the issue no longer reproduces: `verified-fixed`
7. If the issue still reproduces: `still-open`

### For Checks (status: pass)

1. Re-run the evidence command from the manifest
2. Take new screenshot at same URL + viewport
3. Run pixel-diff against the original baseline
4. If still passing and screenshots match: `still-passing`
5. If now failing or screenshots diverge significantly: `regressed`

## Cycle Progression

```
Cycle 1:
  Read manifest.yaml → verify all items → write verification-report.yaml
  If verdict == CLEAR → done
  If verdict == ISSUES_REMAIN → feed still-open items back to adversarial-qa

Cycle 2 (scoped re-QA):
  adversarial-qa runs again, scoped ONLY to still-open/regressed items
  Produces updated manifest.yaml
  adversarial-verify runs again → write verification-report.yaml
  If verdict == CLEAR → done
  If verdict == ISSUES_REMAIN → escalate to user with full evidence trail
```
