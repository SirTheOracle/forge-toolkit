---
name: adversarial-verify
description: >
  Single-agent verification skill that re-runs all tests and screenshots from a QA manifest,
  performs pixel-diff comparisons against baselines, and produces a structured
  `verification-report.yaml`.
---

# Adversarial Verify

## Overview

Single-agent verification skill that re-runs all tests and screenshots from a QA manifest, performs pixel-diff comparisons against baselines, and produces a structured verification report. This is the feedback loop that confirms fixes actually work and catches regressions.

## When to Use

- After adversarial-qa produces a `manifest.yaml` and issues have been fixed
- As part of the automated QA loop (qa → fix → verify → qa if needed)
- To regression-check a previously clean QA pass after new changes

## Prerequisites

- **Project config**: `.claude/forge-project.yml` must exist (provides ports, paths, commands)
- **Running application**: Backend on `{{backend_url}}`, frontend on `{{frontend_url}}`
- **Playwright installed**: `cd {{frontend_working_dir}} && {{playwright_install}}`
- **pixelmatch available**: `cd {{pixelmatch_working_dir}} && npm list pixelmatch || {{pixelmatch_install}}`
- **A manifest.yaml**: Produced by adversarial-qa in `.dev/qa/{slug}/manifest.yaml`

### Step 0: Load Project Config

Before proceeding, read `.claude/forge-project.yml` and build the placeholder substitution map. All `{{tokens}}` in this skill refer to values from that config. See the adversarial-qa skill's "Config Substitution" section for the full token reference.

## Input

The skill takes a path to a QA output directory:

```
/adversarial-verify .dev/qa/{slug}
```

It reads `manifest.yaml` from that directory and verifies every finding and check.

## Process

### Step 1: Load Manifest and Prepare

1. Read `manifest.yaml` from the QA directory
2. Determine the cycle number:
   - If no `verification-report.yaml` exists → cycle 1
   - If `verification-report.yaml` exists with `verdict: ISSUES_REMAIN` → cycle 2
   - If cycle 2 already exists → escalate to user (max 2 cycles)
3. Create evidence directory for this cycle: `evidence/verify-cycle-{N}/screenshots/` and `evidence/verify-cycle-{N}/diffs/`
4. Verify environment (backend + frontend running)

### Step 2: Verify Findings

For each finding with `status: open`:

1. **Re-run reproduction steps** from the manifest
2. **For each evidence item**, re-execute based on type:

   **Screenshot evidence:**
   ```bash
   # Re-take at same URL and viewport
   npx playwright screenshot \
     --viewport-size={width},{height} \
     {url} \
     evidence/verify-cycle-{N}/screenshots/{id}-recheck.png
   ```

   **API response evidence:**
   ```bash
   # Re-curl the endpoint
   curl -s -o evidence/verify-cycle-{N}/api-responses/{id}-recheck.json \
     -w "%{http_code}" \
     -X {method} {url}
   ```

   **Test output evidence:**
   ```bash
   # Re-run the exact test command
   {command} 2>&1 | tee evidence/verify-cycle-{N}/test-results/{id}-recheck.txt
   ```

3. **Run pixel-diff** for screenshot evidence (see Step 4)
4. **Determine status**:
   - Issue no longer reproduces → `verified-fixed`
   - Issue still reproduces → `still-open`

### Step 3: Verify Checks (Regression Detection)

For each check with `status: pass`:

1. Re-run the evidence command from the manifest
2. Re-take screenshot at the same URL + viewport
3. Run pixel-diff against the baseline
4. Determine status:
   - Still passing + screenshots match → `still-passing`
   - Now failing or screenshots diverge → `regressed`

### Step 4: Pixel-Diff Comparison

For every screenshot comparison, write and execute a Node.js script using pixelmatch:

```javascript
// verify-diff.mjs -- written by the agent, executed via node
import { readFileSync, writeFileSync } from "fs";
import { PNG } from "pngjs";
import pixelmatch from "pixelmatch";

const baseline = PNG.sync.read(readFileSync(process.argv[2]));
const current = PNG.sync.read(readFileSync(process.argv[3]));
const diffPath = process.argv[4];

// Handle size mismatches by using the larger dimensions
const width = Math.max(baseline.width, current.width);
const height = Math.max(baseline.height, current.height);

// Create canvases at max size, fill with white for size-mismatch areas
function padImage(img, w, h) {
  if (img.width === w && img.height === h) return img;
  const padded = new PNG({ width: w, height: h });
  // Fill white
  for (let i = 0; i < padded.data.length; i += 4) {
    padded.data[i] = 255; padded.data[i+1] = 255;
    padded.data[i+2] = 255; padded.data[i+3] = 255;
  }
  PNG.bitblt(img, padded, 0, 0, img.width, img.height, 0, 0);
  return padded;
}

const b = padImage(baseline, width, height);
const c = padImage(current, width, height);
const diff = new PNG({ width, height });

const numDiffPixels = pixelmatch(b.data, c.data, diff.data, width, height, {
  threshold: 0.1,
  alpha: 0.3,
  diffColor: [255, 0, 255],
});

writeFileSync(diffPath, PNG.sync.write(diff));

const totalPixels = width * height;
const mismatchPct = ((numDiffPixels / totalPixels) * 100).toFixed(2);

// Output as JSON for the agent to parse
console.log(JSON.stringify({
  mismatch_pixels: numDiffPixels,
  total_pixels: totalPixels,
  mismatch_percentage: parseFloat(mismatchPct),
  baseline_size: { width: baseline.width, height: baseline.height },
  current_size: { width: current.width, height: current.height },
}));
```

Run with:
```bash
node verify-diff.mjs \
  evidence/screenshots/C-001-dashboard-load.png \
  evidence/verify-cycle-1/screenshots/C-001-recheck.png \
  evidence/verify-cycle-1/diffs/C-001-diff.png
```

**Match threshold**: `mismatch_percentage` < 5.0% = match. This accounts for anti-aliasing and minor rendering differences.

**Size mismatch**: If baseline and current have different dimensions, this is itself a signal of regression. The diff image pads to the larger size and the mismatch will be high.

### Step 5: Produce Verification Report

Write `verification-report.yaml` to the QA directory. See `references/verification-report-format.md` for the full schema.

Key fields:
- `verdict`: `CLEAR` if all findings are `verified-fixed` and all checks are `still-passing`; `ISSUES_REMAIN` otherwise
- `summary`: Counts of each status
- `finding_results`: Per-finding verification with evidence
- `check_results`: Per-check regression verification with evidence

### Step 6: Determine Next Action

| Verdict | Cycle | Action |
|---------|-------|--------|
| `CLEAR` | 1 or 2 | Done. Report clean bill of health to lead. |
| `ISSUES_REMAIN` | 1 | Return `still-open` and `regressed` item IDs to lead for scoped re-QA. |
| `ISSUES_REMAIN` | 2 | Escalate to user. Present full evidence trail across both cycles. |

## Pixel-Diff Dependencies

The verification agent needs `pixelmatch` and `pngjs`. Install if missing:

```bash
cd {{pixelmatch_working_dir}} && npm list pixelmatch pngjs 2>/dev/null || {{pixelmatch_install}}
```

If these packages cannot be installed, fall back to ImageMagick:

```bash
compare -metric AE \
  evidence/screenshots/C-001-dashboard-load.png \
  evidence/verify-cycle-1/screenshots/C-001-recheck.png \
  evidence/verify-cycle-1/diffs/C-001-diff.png 2>&1
```

## Non-Screenshot Verification

Not all evidence is screenshots. The verification agent also re-runs:

| Evidence Type | Verification Method | Pass Condition |
|---------------|-------------------|----------------|
| `screenshot` | Re-take + pixel-diff | mismatch < threshold |
| `api-response` | Re-curl endpoint | Status code matches expected (200 for checks, not-500 for findings) |
| `test-output` | Re-run command | Exit code 0 for checks; exit code changed for findings |
| `console-error` | Re-load page, capture console | No errors for checks; error gone for findings |
| `command` | Re-run command | Exit code matches expected |

## Error Handling

| Failure | Recovery |
|---------|----------|
| Backend/frontend not running | Start them before proceeding |
| Playwright not installed | Run `npx playwright install chromium` |
| pixelmatch not installed | Install it or fall back to ImageMagick |
| Screenshot fails (page error) | Record as `regressed` with the error |
| Baseline file missing | Skip pixel-diff, take fresh screenshot, note in report |
| Manifest malformed | Abort and report parse error to lead |

## Output

```
.dev/qa/{slug}/
  verification-report.yaml    ← the deliverable
  evidence/
    verify-cycle-{N}/
      screenshots/             ← re-taken screenshots
      diffs/                   ← pixel-diff images
      api-responses/           ← re-curl results
      test-results/            ← re-run test output
```

## Invoking the Skill

```
/adversarial-verify .dev/qa/gallery-page
```

Or programmatically from the QA loop:
```
Use adversarial-verify to verify the QA findings in .dev/qa/gallery-page/manifest.yaml
```
