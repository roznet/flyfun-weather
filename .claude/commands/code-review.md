Review target: $ARGUMENTS

If $ARGUMENTS is a GitHub PR URL or repo/pull/number:
- Use `gh` to fetch the PR diff and description
- Focus review on changed lines only
- Post the review as a PR comment using `gh pr comment`

Otherwise:
- Interpret $ARGUMENTS as a description of what to review
- Use git log, git diff, or file reads as appropriate to 
  find the relevant changes

Then apply the standard review criteria:

Before starting the review, read the following for context:
- CLAUDE.md at the repo root for coding standards
- Any design documents in /designs/ folder relevant to files being changed
- use the designs documents for module intent and architecture

Then perform a code review of the current PR focusing on:
- Bugs and logic errors
- CLAUDE.md violations
- Deviations from the documented architecture/design intent from the designs documents
- Swift/iOS best practices (memory management, concurrency)
- TypeScript: type safety, avoid `any`, async/await correctness
- Python: type hints, error handling, async patterns if applicable
- Code and logic duplication, opportunity for optimisation and consolidation
- Check for simplicity, maintainability and extensibility

Do NOT flag:
- Style issues not covered by CLAUDE.md
- Minor suggestions or nits
- Pre-existing issues not touched by this PR

Be concise. High confidence issues only.

If no issues found, post a brief approval comment.
