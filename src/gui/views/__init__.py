# -*- coding: utf-8 -*-
"""GUI 标签页视图组件。"""

from .shelf_view import ShelfView
from .queue_view import QueueView
from .history_view import HistoryView
from .reader_view import ReaderView
from .weread_view import WereadView
from .calibre_view import CalibreView
from .notebook_view import NotebookView
from .limitations_view import LimitationsView
from .stats_view import StatsView
from .help_view import HelpView
from .plugins_view import PluginsView

__all__ = [
    "ShelfView", "QueueView", "HistoryView", "ReaderView",
    "WereadView", "NotebookView", "CalibreView", "LimitationsView",
    "StatsView", "HelpView", "PluginsView",
]
