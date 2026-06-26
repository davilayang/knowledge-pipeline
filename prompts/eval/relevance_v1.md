You are grading whether a wiki page stays ABOUT "{entity}" or drifts into other
subjects. Split the PAGE into contiguous passages (a sentence or a few). For EACH
passage decide whether it is primarily about {entity}.

A passage is on_topic if {entity} is its subject, or if it describes something
{entity} did, made, said, or is part of. A passage is NOT on_topic if its real
subject is a different entity or topic that merely co-occurs with {entity} — name
that subject. Background that frames {entity} is on_topic; a digression into an
unrelated project, person, or event is not.

Return JSON with a "passages" array; each item has "text" (the passage),
"on_topic" (boolean), and "subject" (the off-topic subject when on_topic is
false, else null).

PAGE:
{page}
