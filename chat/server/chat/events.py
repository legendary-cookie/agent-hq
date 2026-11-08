"""Live updates to browsers. Each event names the users allowed to see it; None means everyone signed in."""
import asyncio
import json
import threading


class Hub:
    def __init__(self):
        self.loop = None
        self.subs = {}  # queue -> user_id
        self.lock = threading.Lock()
        self.agent_waiters = {}  # agent_id -> set of asyncio.Event

    def attach(self, loop):
        self.loop = loop

    def subscribe(self, user_id):
        queue = asyncio.Queue(maxsize=500)
        with self.lock:
            self.subs[queue] = user_id
        return queue

    def unsubscribe(self, queue):
        with self.lock:
            self.subs.pop(queue, None)

    def publish(self, kind, data, users=None):
        """Safe to call from any thread."""
        if self.loop is None:
            return
        text = json.dumps({'type': kind, 'data': data}, default=str)
        with self.lock:
            targets = [q for q, uid in self.subs.items() if users is None or uid in users]
        for queue in targets:
            self.loop.call_soon_threadsafe(_put, queue, text)

    # Agents long-poll for work; wake them when a delivery is queued.
    def agent_waiter(self, agent_id):
        event = asyncio.Event()
        with self.lock:
            self.agent_waiters.setdefault(agent_id, set()).add(event)
        return event

    def drop_waiter(self, agent_id, event):
        with self.lock:
            self.agent_waiters.get(agent_id, set()).discard(event)

    def wake_agent(self, agent_id):
        if self.loop is None:
            return
        with self.lock:
            events = list(self.agent_waiters.get(agent_id, ()))
        for event in events:
            self.loop.call_soon_threadsafe(event.set)


def _put(queue, text):
    try:
        queue.put_nowait(text)
    except asyncio.QueueFull:
        pass  # A stuck browser tab drops events; it reloads on reconnect.
