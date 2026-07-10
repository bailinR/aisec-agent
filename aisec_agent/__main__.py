#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/4/16 16:40
# @Author  : GuJR
# @Site    : 
# @File    : __main__.py
from typing import Iterable, Tuple

from ugly_code.cmd import CMDHolder
from ugly_code.runit import Runner
from ugly_code.tool import load_variables

from aisec_agent.config import DISABLED_WORKERS

__serve_variables = (
    ("FileProcessor", ".worker:FileProcessWorker",),
   # ("VibeVoiceProcessor", ".worker:VibeVoiceProcessWorker",),
)


def run_it(workers: Iterable[Tuple[str, str]]):
    workers = tuple(workers)
    Runner(fork=tuple(
        zip(map(lambda it: it[0], workers), load_variables(tuple(map(lambda it: it[1], workers)), __package__))
    )).run()


@CMDHolder.command('serve')
def serve():
    run_it(filter(lambda it: it[0] not in DISABLED_WORKERS, __serve_variables))


if __name__ == '__main__':
    CMDHolder().execute()
