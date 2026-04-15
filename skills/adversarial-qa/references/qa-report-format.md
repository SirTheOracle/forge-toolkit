# QA Report Format

Every QA report (A and B) must follow this structure. This ensures reports are comparable, reviewable, and machine-parseable by the adversarial-verify skill.

## Evidence Directory

All evidence MUST be saved to the QA output directory -- NOT to `/tmp/`. Use the naming conventions from the manifest schema.

```
{output_dir}/
  evidence/
    screenshots/
      {id}-{short-slug}.png       e.g., F-A01-gallery-lightbox-mobile.png
    api-responses/
      {id}-{short-slug}.json      e.g., F-A02-stage6-audio-500.json
    test-results/
      {id}-{short-slug}.txt       e.g., C-A01-playwright-dashboard.txt
```

**Tester A** uses IDs prefixed `F-A` (findings) and `C-A` (checks), numbered sequentially: `F-A01`, `F-A02`, `C-A01`, `C-A02`, etc.

**Tester B** uses IDs prefixed `F-B` and `C-B`.

The synthesizer will renumber these to `F-001`, `C-001` in the merged manifest.

## Standard Viewports

Use these exact viewport sizes for all screenshots so verification can reproduce them:

- **Desktop**: `--viewport-size=1280,720`
- **Mobile**: `--viewport-size=375,812`

## Required Sections

### 1. Test Scope Summary

Restate what you were asked to test and your assigned strategy.

- What areas of the application are in scope?
- What changes or features triggered this QA pass?
- Your assigned strategy (UI Regression or Functional Integration)

### 2. Environment Verification

Before testing, confirm the environment is working:

- Backend running? (curl health check)
- Frontend running? (page loads?)
- Database accessible? (API returns data?)
- Any environment issues that affected testing?

### 3. Test Execution Log

Walk through every test you ran, in order. Each test produces BOTH a markdown entry AND a manifest entry.

```markdown
### Test: [Test Name / Description]

**ID**: C-A01 (or F-A01 if it's a finding)
**Type**: Playwright screenshot | Playwright spec | pytest | curl | Manual
**Command**: `exact command you ran`
**Result**: PASS | FAIL | FLAKY (1/3) | SKIPPED
**Duration**: ~Xs

**Evidence saved**:
- Screenshot: `evidence/screenshots/C-A01-dashboard-load.png`
- Test output: `evidence/test-results/C-A01-playwright-output.txt`

**Output** (for failures):
\`\`\`
paste relevant output here
\`\`\`

**Notes**: Any observations about the test result.
```

### 4. Issues Found

For each issue discovered during testing:

```markdown
### Issue: [Brief Title]

**ID**: F-A01
**Severity**: CRITICAL | HIGH | MEDIUM | LOW | INFO
**Category**: ui-regression | api-contract | data-flow | feature-bug | console-error | performance
**Reproducible**: always | intermittent (X/Y runs) | once
**Affected Area**: /dashboard | /gallery | Stage N | API endpoint

#### Description
What the issue is and how you found it.

#### Reproduction Steps
1. Navigate to ...
2. Click on ...
3. Observe that ...
   - Expected: ...
   - Actual: ...

#### Evidence
- Screenshot: `evidence/screenshots/F-A01-gallery-lightbox-mobile.png` (viewport: mobile, url: {{frontend_url}}/gallery)
- Test output: `evidence/test-results/F-A01-playwright-output.txt` (command: `npx playwright test gallery.spec.ts`, exit_code: 1)
- HTTP response: `evidence/api-responses/F-A01-stage6-audio-500.json` (method: GET, url: /api/stages/6/audio, status_code: 500)
- Console error: `evidence/test-results/F-A01-console-errors.txt` (url: {{frontend_url}}/gallery)

#### Possible Cause
Brief hypothesis about what's causing this (if obvious from symptoms).
```

### 5. Test Coverage Summary

What you tested and what you didn't:

```markdown
| ID | Area | Tested? | Method | Result |
|----|------|---------|--------|--------|
| C-A01 | Dashboard loads | Yes | Playwright screenshot | PASS |
| C-A02 | Stage 1 loads | Yes | Playwright screenshot | PASS |
| ... | ... | ... | ... | ... |
| -- | Stage 9 assembly | No | -- | Not in scope |
```

### 6. Per-Tester Manifest

**CRITICAL**: At the end of your report, produce a YAML manifest block that the synthesizer will merge. This manifest is what the verification skill consumes.

````markdown
## Manifest

```yaml
findings:
  - id: "F-A01"
    title: "Gallery lightbox crops video on mobile"
    severity: "MEDIUM"
    status: "open"
    category: "ui-regression"
    affected_area: "/gallery"
    reproducible: "always"
    repro_steps:
      - "Navigate to /gallery"
      - "Click any video card"
      - "Observe lightbox on 375px viewport"
    expected: "Full video visible without cropping"
    actual: "Bottom 20% of video cut off"
    evidence:
      - type: "screenshot"
        path: "evidence/screenshots/F-A01-gallery-lightbox-mobile.png"
        viewport: "mobile"
        url: "{{frontend_url}}/gallery"
        description: "Video cropped at 375px viewport"

checks:
  - id: "C-A01"
    description: "Dashboard loads without console errors"
    status: "pass"
    category: "ui-regression"
    affected_area: "/dashboard"
    evidence:
      - type: "screenshot"
        path: "evidence/screenshots/C-A01-dashboard-load.png"
        viewport: "desktop"
        url: "{{frontend_url}}/dashboard"
        description: "Dashboard renders correctly"
      - type: "command"
        command: "npx playwright screenshot --full-page --viewport-size=1280,720 {{frontend_url}}/dashboard evidence/screenshots/C-A01-dashboard-load.png"
        exit_code: 0
```
````

### 7. Overall Assessment

- **Confidence level**: HIGH | MEDIUM | LOW -- how confident are you in the overall quality?
- **Recommendation**: SHIP IT | FIX FIRST | NEEDS MORE TESTING
- **Top concern**: What's the most important thing to address?
- **Missing coverage**: What couldn't you test and why?

## Severity Definitions

| Severity | Definition | Example |
|----------|-----------|---------|
| **CRITICAL** | App crashes, data loss, security hole | White screen on dashboard, deleted user data |
| **HIGH** | Feature broken, blocks user workflow | Can't approve outline, generation button does nothing |
| **MEDIUM** | Degraded experience, workaround exists | Wrong styling, slow load, minor data display error |
| **LOW** | Cosmetic, minor inconvenience | Alignment off, hover state missing |
| **INFO** | Not a bug but worth noting | Flaky test, slow API response, deprecation warning |

## Format Guidelines

- Use markdown headers and tables
- Include exact commands so tests can be reproduced
- Reference evidence files by their path in the evidence directory (NOT /tmp/)
- Keep evidence inline -- don't make the reader hunt for it
- Target 200-500 lines for a thorough report
- The YAML manifest block at the end is mandatory -- verification depends on it
