# -*- coding: utf-8 -*-
"""双击/命令行运行：python 启动GUI.py"""

import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, SRC_DIR)

from gui.main import main

if __name__ == "__main__":
    main()
