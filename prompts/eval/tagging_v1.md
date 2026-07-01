You are grading whether each claim's tag — `[reported]` or `[opinion]` — is
correct, given the source the claim was drawn from. A claim extractor tagged
each claim; your job is to say what the tag SHOULD be, on the source's own terms.

Rules (these are the extractor's own rubric — you are checking it followed them):

- `reported` — the source presents it as established, reported, or having actually
  happened: a named event, a release, a number, a measured result, an attributed
  quote. Reported results stay `reported` even from one source. ("Reported" is
  about the source's stance, NOT whether the claim is true.)
- `opinion` — the source presents it as a prediction, forecast, opinion,
  recommendation, marketing pitch, or otherwise unverified.

Look THROUGH reported speech and embedded editorializing:
- A reported prediction/opinion is `opinion`, not `reported`, even though it is
  true that someone said it: "X predicts Y", "X hopes Y", "X believes Y", "X
  characterizes/describes/frames Z as …" → grade the embedded claim.
- A statement of fact fused with a lesson or judgment is `opinion` if the
  judgment is the point: "the team was three people, **showing that** small teams
  achieve breakthroughs" → opinion (the "showing that …" is the stance). Bare
  superlatives ("the leading product", "significant progress") are `opinion`.
- Forward-looking analogies ("X is the new electricity", "X will transform Y")
  are `opinion`.

Grade what the SOURCE supports, not whether the claim is true in the world.

## Source

{source}

## Claims (numbered, with the extractor's tag)

{claims}

Return ONLY a JSON object with a `verdicts` array — exactly one entry per
numbered claim above, each giving that claim's `claim_number` and the correct
tag. Include every claim number from 1 to the last, and do not invent extra
numbers:

{{"verdicts": [{{"claim_number": 1, "correct_tag": "reported"}}, {{"claim_number": 2, "correct_tag": "opinion"}}]}}
