import dagster as dg

from .assets import all_assets
from .checks import all_checks
from .resources import build_resources
from .schedules import extract_complex_contents_job
from .sensors import all_sensors

defs = dg.Definitions(
    assets=all_assets,
    asset_checks=all_checks,
    jobs=[extract_complex_contents_job],
    sensors=all_sensors,
    resources=build_resources(),
)
