"""Entry point of the service: python -m mine.modules.advisor"""
from mine.modules.advisor.core import AdvisorCore
from mine.modules.runtime import run

raise SystemExit(run(AdvisorCore))
