You are grading whether each claim's tag — `[fact]` or `[speculation]` — is
correct, given the source the claim was drawn from. A claim summariser tagged
each claim; your job is to say what the tag SHOULD be, on the source's own terms.

Rules (these are the summariser's own rubric — you are checking it followed them):

- `fact` — the source presents it as established, reported, or having actually
  happened: a named event, a release, a number, a measured result, an attributed
  quote. Reported results stay `fact` even from one source.
- `speculation` — the source presents it as a prediction, forecast, opinion,
  recommendation, marketing pitch, or otherwise unverified.

Look THROUGH reported speech and embedded editorializing:
- A reported prediction/opinion is `speculation`, not `fact`, even though it is a
  fact that someone said it: "X predicts Y", "X hopes Y", "X believes Y", "X
  characterizes/describes/frames Z as …" → grade the embedded claim.
- A factual statement fused with a lesson or judgment is `speculation` if the
  judgment is the point: "the team was three people, **showing that** small teams
  achieve breakthroughs" → speculation (the "showing that …" is opinion). Bare
  superlatives ("the leading product", "significant progress") are `speculation`.
- Forward-looking analogies ("X is the new electricity", "X will transform Y")
  are `speculation`.

Grade what the SOURCE supports, not whether the claim is true in the world.

## Source

{source}

## Claims (numbered, with the summariser's tag)

{claims}

Return ONLY a JSON object with a `verdicts` array — one entry per claim, in the
SAME ORDER and SAME COUNT as the numbered claims above, each giving the correct
tag:

{{"verdicts": [{{"correct_tag": "fact"}}, {{"correct_tag": "speculation"}}]}}
