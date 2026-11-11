import json
from pathlib import Path
from typing import Any
from .repository import ElectionContextRepository

def import_events_jsonl(repo: ElectionContextRepository, path: str | Path):
    path = Path(path)
    if not path.exists():
        print(f'events JSONL not found: {path}')
        return 0
    count = 0
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            eid = repo.save_event(event)
            for src in event.get('sources', []):
                sid = repo.save_source(src)
                repo.link_event_source(eid, sid, src.get('is_primary', False))
            count += 1
    repo.conn.commit()
    return count

def import_sources_jsonl(repo: ElectionContextRepository, path: str | Path):
    path = Path(path)
    if not path.exists():
        print(f'sources JSONL not found: {path}')
        return 0
    count = 0
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            src = json.loads(line)
            repo.save_source(src)
            count += 1
    repo.conn.commit()
    return count
