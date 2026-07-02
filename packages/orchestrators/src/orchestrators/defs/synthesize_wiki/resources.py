# Resources for the synthesize_wiki pipeline.
#
# The "wiki" resource (WikiResource) was re-homed to `orchestrators.defs.shared`
# so it survives this pipeline's retirement (3e cutover). This module now only
# re-exports WikiResource for the assets' type annotations and binds NO resources
# of its own — `shared.defs` provides "wiki" at the top-level Definitions.merge
# (binding it here too would collide on the resource key).

import dagster as dg

from orchestrators.defs.shared.resources import WikiResource  # noqa: F401


def build_resources() -> dict[str, dg.ConfigurableResource]:
    # "wiki" is bound by shared.defs; this pipeline declares none.
    return {}
