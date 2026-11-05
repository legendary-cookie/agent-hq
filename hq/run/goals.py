"""Goal data helpers."""
from dataclasses import asdict
import json


def plan_json(plan):
    return json.dumps(asdict(plan), sort_keys=True, separators=(',', ':'))
