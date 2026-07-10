# PR workflow

This repo uses a simple branch → PR → review → merge cycle. Conventions below are
descriptive of what's actually been used across PRs #1-#4, not aspirational.

## Branches

- One feature/change per branch, branched off `master`.
- Name branches for what they add, e.g. `stations-ships-overlay`,
  `straits-gfw-integration`.
- Don't push directly to `master` outside of merging a PR.

## Opening a PR

Every PR description has two sections:

- **Summary** — bullet points of what changed and why. State scope limits
  explicitly (e.g. "this shows X, not Y") rather than letting them be implied.
- **Test plan** — a checklist of what was actually run/verified locally, not
  what should theoretically work. If something wasn't tested end-to-end, say so
  rather than implying full coverage.

## Handling review comments

- Verify a reviewer's claim independently before responding to it - re-derive
  the numbers, trace the actual source/call chain, don't just trust or
  concede based on how confidently it's stated.
- If the claim checks out, fix it and say what commit fixed it.
- If it doesn't check out (or references something that isn't actually in the
  repo), say so plainly, without being defensive.
- When a fix turns out to be wrong on a second look (it happens - see the
  Chrome-fallback exchange on PR #2), say so directly and re-verify from
  source rather than re-guessing.

## Merging

- Merge once open comments are addressed. A PR being "clean"/mergeable in
  GitHub's UI doesn't by itself mean the discussion is resolved - check the
  thread.
