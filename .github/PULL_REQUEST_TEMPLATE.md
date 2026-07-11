<!--
  Issue-linking is REQUIRED for anything that resolves a tracked issue.
  Recent PRs referenced issues as a bare "#N" (or only in the title) with no
  close keyword — so GitHub never auto-closed them AND the deploy/archive skills
  (which match a keyword whitelist, not bare "#N") skipped them too, leaving
  shipped work open. Pick the right keyword below and put it in BOTH this body
  and the commit message (rebase-merge preserves commit bodies, so GitHub reads
  either one).
-->

## What & why


## Issue linkage — choose one keyword (see comment above)

<!--
  This project deploys `main` continuously via the /deploy skill, and ships iOS
  through the App Store — so "merged" is NOT "live for users". Match the keyword
  to WHEN the work actually reaches users:

  • Web / server / MCP work  →  `Addresses #N`
      Not live at merge; goes live on the next deploy. The /deploy skill closes
      it AFTER the health check passes. (Do NOT use `Closes #N` — that closes at
      merge, before the deploy.)

  • iOS-only work            →  `Addresses #N`  (never `Closes #N`)
      Not live until Apple APPROVES the build. The /archive skill closes it after
      approval (Step 11). If an iOS-only issue should not be swept up by a server
      deploy, say so in the body so /deploy leaves it for /archive.

  • Docs / tests / refactor with no user-facing surface  →  `Closes #N`
      Fine to close at merge — there's nothing to deploy or approve.

  • Passing reference only (context, not resolved)  →  bare `#N` or "see #N"
      Intentionally NOT matched by any close automation.
-->

Addresses #

## Testing / verification

