"""Entry point of the service: python -m mine.modules.grade"""
from mine.modules.grade.core import GradeCore
from mine.modules.runtime import run

raise SystemExit(run(GradeCore))
