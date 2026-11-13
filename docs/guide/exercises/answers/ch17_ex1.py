#!/usr/bin/env python3

"""
ch17_ex1.py --- reference answer to exercise 17.1: find a module through the top-level API

Contains:
    solve(): returns the module that defines suggest_swaps
"""

import stage


def solve() -> str:
    """Returns the module that defines suggest_swaps."""
    return str(stage.suggest_swaps.__module__)
