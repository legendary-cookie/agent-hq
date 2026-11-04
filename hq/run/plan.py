"""Parse planner output as untrusted data."""
import hashlib
import json
import re
from dataclasses import asdict, dataclass


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    prompt: str
    kind: str
    depends_on: tuple[str, ...]
    tool: str | None
    level: str


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...]


def planner_prompt(goal_text, max_steps):
    return (f'Plan this goal in at most {max_steps} steps. Return one fenced json block '
            'with {"steps":[{"id":"s1","title":"...","prompt":"...",'
            '"kind":"code|review|research","depends_on":[],"tool":null}]}.\n'
            f'Goal:\n{goal_text}')


def parse_plan(output_text, max_steps, max_level, tools):
    levels = ('read_only', 'ask', 'edit')
    if type(max_steps) is not int or not 1 <= max_steps <= 10 or max_level not in levels:
        raise PlanError('Invalid goal limits')
    if not isinstance(output_text, str):
        raise PlanError('Expected planner output')
    blocks = re.findall(r'```json\s*\n(.*?)```', output_text, re.DOTALL)
    if not blocks:
        raise PlanError('Expected a fenced JSON block')
    try:
        data = json.loads(blocks[-1])
    except (ValueError, RecursionError) as exc:
        raise PlanError('Invalid JSON') from exc
    if not isinstance(data, dict) or not isinstance(data.get('steps'), list) or not 1 <= len(data['steps']) <= max_steps:
        raise PlanError('Invalid step count')
    steps = []
    for item in data['steps']:
        if not isinstance(item, dict): raise PlanError('Invalid step')
        sid, title, prompt = item.get('id'), item.get('title'), item.get('prompt')
        kind, deps, tool = item.get('kind'), item.get('depends_on'), item.get('tool')
        if not isinstance(sid, str) or not re.fullmatch(r's[0-9]{1,2}', sid): raise PlanError('Invalid step id')
        if not isinstance(title, str) or not title.strip() or len(title) > 120: raise PlanError('Invalid title')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000: raise PlanError('Invalid prompt')
        if kind not in ('code', 'review', 'research'): raise PlanError('Invalid kind')
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps) or len(set(deps)) != len(deps): raise PlanError('Invalid dependencies')
        if tool is not None and (not isinstance(tool, str) or tool not in tools): raise PlanError('Invalid tool')
        level = levels[min(levels.index(max_level), 2 if kind == 'code' else 0)]
        steps.append(Step(sid, title, prompt, kind, tuple(deps), tool, level))
    ids = {s.id for s in steps}
    if len(ids) != len(steps) or any(d not in ids for s in steps for d in s.depends_on): raise PlanError('Invalid dependencies or duplicate id')
    done = set()
    while len(done) < len(steps):
        ready = {s.id for s in steps if s.id not in done and set(s.depends_on) <= done}
        if not ready: raise PlanError('Dependency cycle')
        done.update(ready)
    return Plan(tuple(steps))


def plan_hash(plan):
    payload = json.dumps(asdict(plan), sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
