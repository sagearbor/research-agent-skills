"""SYNTHETIC EVAL FIXTURE — the safe half, deliberately.

Decoy surface: config loading is done correctly and must NOT be reported.
"""
import yaml


def load(path):
    with open(path) as fh:
        return yaml.safe_load(fh)
