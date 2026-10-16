"""Entry point of the service: python -m mine.modules.dispatch"""
from mine.modules.dispatch.core import DispatchCore
from mine.modules.runtime import run

raise SystemExit(run(DispatchCore))
