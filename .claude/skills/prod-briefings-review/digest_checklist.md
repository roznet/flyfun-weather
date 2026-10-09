# Digest review checklist

The prompt a reviewer subagent gets, and the numbering the record uses. Keep the numbers
stable: the record tallies majors by weakness number across runs, so renumbering breaks the
trend. Add a new weakness at the end; retire one by marking it, never by reusing its number.

## Weaknesses

1. **Runway wind misread.** Headwind reported as crosswind (or back), or a gust paired with the
   mean-wind crosswind component ("gusts to 36 kt give a 16 kt crosswind": the advisory's
   16 kt is the mean wind).
2. **Convection misjudged.** Firing model schemes hidden behind "no Convective Character",
   observed TCU/CB waved off, or the reverse: convection invented, or a radar class
   restated as a severity.
3. **One model presented as the picture.** An advisory marked `(minority view — only X …)` or
   `(outlier: …)` written as agreement ("across models", "the models show"), or its
   figures used as the headline number.
4. **Observations vs forecast.** A benign tone despite worse same-day observations, or a
   forecast kept although observations contradict it, without saying why.
5. **Plumbing or first person.** Advisory names, "aggregate", rule talk in the prose;
   "our / we / I".
6. **Ungrounded or wrong detail.** A number, place, time or attribution the context does not
   support.
7. **Grade not defensible.** Given the advisories and PILOT CAPABILITY, the colour (or the
   reason given for it) does not hold.
8. **Text forecast misused.** DWD/AFD detail projected onto the route, the wrong day, or
   German regional detail kept for a non-German route.

Severity: **major** = would mislead a pilot about what matters for this flight (wrong
driver, wrong grade reason, a hazard hidden or invented). **minor** = everything else.

## Reviewer prompt (full review)

Fill in the pack folders and send one subagent per 3–4 packs:

> You are reviewing aviation weather briefing digests written in production, against the exact
> context the model was given. Read-only: do not edit files.
>
> Packs: `<W>/digests/<id>` … Each folder has `digest_context.txt` (the full LLM input),
> `digest.json` (the output), `route_advisories.json`, and `card.json` (why it was picked).
>
> Read each context fully (the header has route, date, lead time, PILOT CAPABILITY), then the
> digest. Check every factual claim against the context. Use the weakness list in
> `.claude/skills/prod-briefings-review/digest_checklist.md` (read it first). Judge whether
> the grade is defensible and whether the text leads with what matters.
>
> Return per pack: one line with route / lead / pilot / grade; whether the grade is
> defensible (1–2 lines); each issue as `[major|minor] #<weakness>`, with a short digest quote
> and the context line that contradicts or fails to support it; one line of strengths. Under
> 300 words per pack. End with a JSON array, one object per pack:
> `{"pack_id": …, "findings": [{"weakness": 3, "severity": "major", "note": "…"}]}`.
> No padding; if a pack has no issues, say so and give an empty findings list.

## Light pass (tail packs, done by the main session)

Per tail pack, read only `card.json`, the digest's `assessment_reason` and the first sentence
of `synoptic`, and the non-GREEN advisory lines in `digest_context.txt` (with their
minority/outlier lines, and any `[SIGNIFICANT]` observation lines). Promote to a full review
when:
- the reason or headline claims agreement, or uses a figure, from a minority view or outlier;
- the grade sits oddly against the advisories (softer than a RED, harsher than all GREEN);
- the headline contradicts an observation line;
- anything reads as plumbing, first person, or an invented place.

Otherwise record it with `"review": "light"` and the findings you saw (often none).
