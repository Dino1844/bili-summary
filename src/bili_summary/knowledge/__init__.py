"""知识层: 概念体系 / 完整性校验。"""

from .concepts import build_concepts
from .coverage import check_coverage

__all__ = ["build_concepts", "check_coverage"]
