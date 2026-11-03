from dataclasses import replace


def prefix_batch(batch, host):
    def prefix(value):
        return f'{host}/{value}' if value is not None else None

    return replace(batch,
                   sessions=[replace(s, id=prefix(s.id), parent_id=prefix(s.parent_id)) for s in batch.sessions],
                   events=[replace(e, session_id=prefix(e.session_id)) for e in batch.events])
