"""Entry point of the service: python -m mine.modules.plant"""
from mine.modules.plant.core import PlantCore
from mine.modules.runtime import run

raise SystemExit(run(PlantCore))
