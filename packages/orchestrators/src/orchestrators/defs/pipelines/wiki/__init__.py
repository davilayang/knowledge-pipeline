# Wiki synthesis code location — LLM-powered knowledge distillation.

import dagster as dg

from .assets import wiki_index_updated, wiki_pending, wiki_synthesized
from .resources import WikiResource

defs = dg.Definitions(
    assets=[wiki_pending, wiki_synthesized, wiki_index_updated],
    resources={"wiki": WikiResource()},
)
