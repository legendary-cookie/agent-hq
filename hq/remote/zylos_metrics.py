"""Turn the scanner's Zylos message metadata into records.

Unlike the file sources, nothing is mirrored: the scanner sends one row of metadata per
message and never reads the message text. Previews stay empty whatever the Zylos preview
setting says, because the text is not available here to show.
"""
import re
from datetime import datetime, timezone

from hq.records import Batch, EventRec, SessionRec

NAME = 'zylos_metrics'
SESSION = 'zylos:comm'
_CHANNEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,39}')


def read_cursor(store, host):
    """Return (last message id, generation). The generation changes when c4.db is replaced."""
    row = store.get_cursor(NAME, host.name)
    return (int(row[0]), int(row[1])) if row else (0, 0)


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp is not text')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def to_batch(answer, host, cursor):
    """Return (batch, more, notes). A malformed answer is dropped without moving the cursor."""
    after, generation = cursor
    if not isinstance(answer, dict) or not answer.get('found'):
        return Batch([], [], [], []), False, []
    rows, max_id = answer.get('rows'), answer.get('max_id')
    if not isinstance(rows, list) or type(max_id) is not int:
        return Batch([], [], [], []), False, ['zylos_metrics: invalid answer']
    if max_id < after:
        # A new c4.db restarts its ids. A new generation keeps old and new rows apart.
        return (Batch([], [], [], [], 0, [(NAME, host.name, 0, generation + 1)]), True,
                ['zylos_metrics: c4.db was replaced, reading it from the start'])
    events, stamps, warnings, last = [], [], 0, after
    for row in rows:
        if (not isinstance(row, list) or len(row) != 5 or type(row[0]) is not int or row[0] <= last):
            return Batch([], [], [], []), False, ['zylos_metrics: invalid answer']
        row_id, direction, channel, failed, timestamp = row
        last = row_id
        try:
            ts = _timestamp(timestamp)
        except (ValueError, OverflowError):
            warnings += 1
            continue
        channel = channel if isinstance(channel, str) and _CHANNEL.fullmatch(channel) else 'other'
        ext_id = f'c4:{generation}:{row_id}'
        stamps.append(ts)
        events.append(EventRec(SESSION, ext_id, ts, 'user' if direction == 'in' else 'assistant', channel,
                               None, None, None, None, None, False, None))
        if failed is True:
            events.append(EventRec(SESSION, ext_id + ':error', ts, 'error', channel,
                                   None, None, None, None, None, False, None))
    # Ended, because this is a log of messages and not a live session: it must not appear as an
    # extra agent in the office or in the working and idle counts.
    sessions = [SessionRec(SESSION, 'zylos', None, None, None, 'Zylos messages', min(stamps), max(stamps),
                           max(stamps), None, f'{host.name}:c4.db')] if stamps else []
    cursors = [(NAME, host.name, last, generation)] if last != after else []
    notes = [f'zylos_metrics: skipped {warnings} rows with bad timestamps'] if warnings else []
    return Batch(sessions, events, [], [], warnings, cursors), bool(answer.get('more')), notes
