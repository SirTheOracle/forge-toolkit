# Proposal Format

Every proposal (A, B, and C) must follow this structure. This ensures proposals are comparable and reviewable.

## Required Sections

### 1. Problem Statement
Restate the problem in your own words. This validates understanding and catches misinterpretation early.

- What is the observed behavior?
- What is the expected behavior?
- What is the impact/severity?

### 2. Investigation Findings

Walk through your investigation step by step:

- What files/code did you examine?
- What did you find?
- What is the data flow relevant to this issue?
- Include specific code references (file, function, line range) where applicable

### 3. Root Cause Analysis

Clearly identify the root cause:

- **Root Cause**: One clear sentence
- **Evidence**: What specific code/behavior proves this is the cause
- **Why this causes the symptom**: Connect the root cause to the observed behavior

### 4. Solution Plan

Step-by-step implementation plan:

- Each step should be a discrete, reviewable change
- Include the specific files to modify
- Describe what changes in each file
- Note the order of operations if it matters

### 5. Risk Assessment

- What could go wrong with this fix?
- What other functionality might be affected?
- Are there edge cases to watch for?

### 6. Testing Strategy

- How do you verify the fix works?
- What regression tests are needed?
- What manual verification steps are required?

## Format Guidelines

- Use markdown headers and lists
- Include code snippets where they clarify the point
- Reference specific files with paths relative to project root
- Keep it actionable — every section should inform what to do next
- Target 300-600 lines for a thorough proposal (shorter for simple bugs, longer for complex features)
