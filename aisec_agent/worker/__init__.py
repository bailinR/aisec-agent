#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/4/16 12:21
# @Author  : GuJR
# @Site    : 
# @File    : __init__.py
try:
    from .worker import (
        FileProcessWorker, VibeVoiceProcessWorker
    )
except ModuleNotFoundError:
    FileProcessWorker = None
    VibeVoiceProcessWorker = None

__all__ = [
    'FileProcessWorker',
    "VibeVoiceProcessWorker"
]

