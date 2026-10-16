"""Entry point of the service: python -m mine.modules.maint"""
from mine.modules.maint.core import MaintCore
from mine.modules.runtime import run

raise SystemExit(run(MaintCore))
