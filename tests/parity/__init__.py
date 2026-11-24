#!/usr/bin/env python3

"""
parity --- compares the working tree's outputs with a v0.4.0 oracle, case by case

Contains:
    cases: the Case record, the case loader and the byte encoding of case outputs
    compare: the gates, the speed check, flipped cases and the report tables
    run: the `nox -s parity` driver that runs both sides and gates them
    oracle_runner: runs one area's cases under one interpreter and writes their outputs
"""
