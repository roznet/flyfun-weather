<!--
  Link the issue with a keyword. Default is `Closes #N` — see below.
  A bare "#N" (or the number only in the PR title) closes NOTHING: GitHub's
  auto-close needs a keyword, and the deploy skill's regex needs one too. That
  gap is what left #364/#366/#371/#379 open long after they shipped.
-->

## What & why


## Issue linkage

<!--
  DEFAULT — `Closes #N`  (use this unless the issue was filed by an outside user)
    Closes at merge, automatically, with zero bookkeeping. ~93% of issues in this
    repo are self-filed working notes: the tracker records whether the WORK is
    done, not whether it's live. "Is it live" is recorded by the deploy comment,
    the ios/* tags, and the What's New release stream — not by the open/closed
    bit. Applies to iOS work too (the ios/* tag says which build shipped it).

  EXCEPTION — `Addresses #N`  (issue filed by an outside reporter)
    Defers the close to deploy, so a reporter who's subscribed isn't told it's
    fixed before they can actually use it. The /deploy skill closes these after
    the health check passes. Rare — about a dozen issues in this project's
    history. Put the keyword in the COMMIT MESSAGE too: rebase-merge preserves
    commit bodies, so a stray `Closes #N` there will auto-close at merge and
    defeat the deferral.

  NEITHER — bare `#N` / "see #N"
    A passing reference, not a resolution. Intentionally matched by no automation.
-->

Closes #

## Testing / verification

