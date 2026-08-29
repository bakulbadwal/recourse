"""Recourse — a first-response agent for fraud victims.

Deterministic core: intake -> casefile -> filings. Optional Strands agent
layer in recourse.agent (install the [agent] extra).
"""

from .casefile import build_casefile, compute_case_id
from .filings import render_all
from .intake import extract
from .models import CaseFile, EvidenceItem, Transaction, VictimInfo, NOT_PROVIDED

__version__ = "0.1.0"

__all__ = [
    "CaseFile",
    "EvidenceItem",
    "Transaction",
    "VictimInfo",
    "NOT_PROVIDED",
    "build_casefile",
    "compute_case_id",
    "extract",
    "render_all",
    "__version__",
]
