"""视频类型插件（Category Plugins / 视频风格）。

每个插件封装一种视频类型的剪辑策略：镜头时长、转场偏好、动画密度等。
"""

from .base import BaseCategoryPlugin
from .registry import CategoryRegistry
from .knowledge_longform import KnowledgeLongformPlugin
from .kichiku_fastcut import KichikuFastcutPlugin
from .digital_review import DigitalReviewPlugin
from .vlog_daily import VlogDailyPlugin

__all__ = [
    "BaseCategoryPlugin",
    "CategoryRegistry",
    "KnowledgeLongformPlugin",
    "KichikuFastcutPlugin",
    "DigitalReviewPlugin",
    "VlogDailyPlugin",
]
