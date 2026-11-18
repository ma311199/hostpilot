---
name: code-review-check
description: Review user supplied code or a diff for evidence based defects and security concerns.
---

# Code review

This Skill reviews only code or a diff included in the request. It reports evidence-based,
actionable findings, ordered by severity, and explains impact and remediation.

It does not inspect the repository, run code or tests, call tools, or modify files. Findings
must not rely on source that was not supplied. If no concrete defect is visible, it says so
and notes the limits of the review.
