# Implementation Document Format

Every implementation document (A, B, and C) must follow this structure. This ensures documents are comparable and the coverage matrix can be validated.

## Required Sections

### 1. Plan Reference

State which `final-plan.md` this implementation is based on. Include the path.

### 2. Plan Items Inventory

List every discrete item from the plan that requires a code change. Number them (P1, P2, P3...). This becomes the left column of the coverage matrix. Nothing should be omitted — if the plan says it, it goes here.

### 3. Implementation Steps

In execution order. Each step:

```markdown
#### Step N: [Brief description]

**Addresses**: P1, P3 (plan items this step covers)
**File**: `src/path/to/file.py`
**Action**: modify | new_file
**Line**: ~123 (approximate, for orientation)

**Diff**:
```python
# old_string
[exact text currently in the file, with enough context to be unique]
```

```python
# new_string
[exact replacement text]
```

**Verification**: `pytest tests/unit/test_foo.py::test_bar -v`
```

#### Diff Guidelines

- Each step must target exactly one file and contain exactly one `old_string/new_string` diff pair
- `old_string` must be an exact match of text currently in the file (copy-paste from reading it)
- Include enough surrounding lines in `old_string` to make it unambiguous (unique in the file)
- `new_string` must be syntactically correct — it would pass the linter
- Preserve exact indentation (spaces/tabs as they appear in the file)
- For new files, still use the normal diff shape:

```python
# old_string
# new file
```

```python
# new_string
[full file content]
```

- Never use a standalone `# new_file` marker
- Never use placeholders such as `...`, `rest of file unchanged`, or partial snippets for new files
- Commit-group tests must be runnable after the steps in that group are applied; do not reference files created in later groups

### 4. Test Specifications

For each test (new or modified):

```markdown
#### Test: test_function_name

**File**: `tests/unit/test_module.py`
**Type**: New | Modified
**Exercises**: Step N (which implementation step this validates)

**Diff** (or full test code if new):
```python
# old_string (for modified tests)
[existing test code]
```

```python
# new_string
[updated test code]
```
```

#### Test Guidelines

- Tests must exercise the actual changed code path, not just check string values
- For modified tests: explain why the old assertion was wrong and why the new one is right
- Include edge case tests, not just the happy path
- Each test should be runnable independently (`pytest path::test_name -v`)
- For brand-new test files, use the same new-file convention as implementation steps: `# old_string` must contain exactly `# new file`, and `# new_string` must contain the full file content

### 5. Coverage Matrix

| Plan Item | Description | Step(s) | Test(s) | Status |
|-----------|-------------|---------|---------|--------|
| P1 | [from plan] | Step 1, 3 | test_foo, test_bar | Covered |
| P2 | [from plan] | Step 2 | test_baz | Covered |
| P3 | [from plan] | — | — | GAP |

**Every row must have Status = Covered.** If any row shows GAP, the implementation document is incomplete.

### 6. Commit Groups

```markdown
#### Commit 1: [commit message]
- Steps: 1, 2, 3
- Tests to run: `pytest tests/unit/test_foo.py -v`
- Why grouped: These steps modify the same function and must land together

#### Commit 2: [commit message]
- Steps: 4, 5
- Tests to run: `pytest tests/unit/test_bar.py -v`
- Why grouped: Independent feature, can be reviewed separately
```

Commit-group rules:
- The test command for a group must be executable immediately after that group's steps
- If a group's test file is created in the implementation, that creation step must be in the same group or an earlier group
- Do not reference a test file in Commit 1 if the file is created in Commit 2

### 7. Execution Order Rationale

Explain why the steps are ordered the way they are. Flag any steps where order matters (e.g., "Step 2 must come before Step 3 because Step 3 calls the function modified in Step 2").

### 8. Definition of Done

```markdown
- [ ] All implementation steps applied
- [ ] `pytest tests/unit/test_affected_module.py -v` — all pass
- [ ] `pytest tests/` — no new failures (may have pre-existing failures)
- [ ] `ruff check src/affected/files.py` — no new lint errors
- [ ] [Any manual verification steps]
```

## Format Guidelines

- Use fenced code blocks with language tags for all diffs
- Mark `old_string` and `new_string` clearly with comments
- Reference files with paths relative to project root
- Keep prose minimal — the diffs and tests are the substance
- Target completeness over brevity — a 500-line implementation doc that covers everything is better than a 200-line one with gaps
- Prefer the stricter shape that `$forge-coder` can execute mechanically over any looser human-readable shorthand
