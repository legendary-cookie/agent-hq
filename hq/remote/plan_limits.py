"""Turn the scanner's plan limit reading into limit records for one agent.

Readings are stored per agent, under the account id "agent:<host>", and feed the usage charts and the
run guard's reset times. Agents now reach Claude through 9router (see the Router page) rather than their
own Claude login, so Claude Code usually has no plan limits to report and the probe finds nothing; that
is expected, and nothing in HQ depends on a reading being there.
"""
from hq.records import Batch, LimitRec

NAME = 'claude_limits'


def agent_account(host_name):
    return f'agent:{host_name}'


def window_of(name):
    """("five_hour" -> "5h", "*"), ("seven_day_opus" -> "week", "opus"). Unknown windows are skipped."""
    for prefix, window in (('five_hour', '5h'), ('seven_day', 'week')):
        if name == prefix:
            return window, '*'
        if name.startswith(prefix + '_'):
            return window, name[len(prefix) + 1:]
    return None


def to_batch(answer):
    if not isinstance(answer, dict) or not answer.get('found'):
        return Batch([], [], [], [])
    captured, windows = answer.get('captured_at'), answer.get('windows')
    if not isinstance(captured, (int, float)) or not isinstance(windows, dict):
        return Batch([], [], [], [])
    limits = []
    for name, value in windows.items():
        shape = window_of(name)
        if not shape or not isinstance(value, list) or len(value) != 2:
            continue
        pct, reset = value
        if isinstance(pct, (int, float)) and 0 <= pct <= 100 and (reset is None or isinstance(reset, (int, float))):
            limits.append(LimitRec('claude_code', 'anthropic', shape[1], shape[0], float(pct), reset, 'measured', float(captured)))
    return Batch([], [], limits, [])
