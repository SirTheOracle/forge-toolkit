# Proposal Format Lite

Every A/B proposal must follow this structure so the lead and synthesizer can compare them reliably.

Target length: 100-350 lines.

## 1. Problem Statement

Restate the problem in your own words.

Include the problem-type-specific details:

| Type | Required details |
|------|------------------|
| Bug fix | Observed vs expected behavior, severity, reproduction or trigger |
| Feature | Capability needed, user/system need, success criteria |
| Architecture | Constraint or goal, current limitation |
| Refactoring | Current state, target state, reason to change |

## 2. Investigation Findings

List what you examined and what you found.

Include:

- Source files read.
- Relevant functions/classes/endpoints.
- Data flow or control flow.
- Files expected to matter but ruled irrelevant, if any.

## 3. Core Analysis

Include:

- **Core Finding:** One clear sentence with confidence.
- **Mechanism:** How the finding causes the symptom or creates the gap.
- **Evidence:** Concrete source references.

Evidence table:

```markdown
| Claim | Source | Confidence |
|-------|--------|------------|
| ... | path/to/file.ext:function():L10-L20 | [HIGH] |
```

Minimum 3 rows.

## 4. Solution Plan

Write implementation steps in execution order.

Each step should include:

- File path.
- Function/class/section target.
- Change to make.
- Why the change is needed.

## 5. Alternatives Considered

Include:

- At least one rejected root cause or hypothesis, with evidence.
- At least one rejected implementation approach, with reason.
- One sentence describing what evidence would falsify your conclusion.

## 6. Risk Assessment

List at least 2 concrete risks.

Each risk must name affected code, endpoints, data paths, commands, or user workflows. Avoid generic statements like "could break existing functionality."

## 7. Testing Strategy

Name specific tests to add or run.

Include:

- Regression tests.
- Edge case tests.
- Manual verification, if needed.
- Exact commands when known.
