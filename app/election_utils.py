import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

def compute_sha256(file_path: str | Path) -> str:
    sha = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            sha.update(chunk)
    return sha.hexdigest()

def load_json(path: str | Path) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def save_json(path: str | Path, data: Any):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def format_taipei_now() -> str:
    from datetime import timezone, timedelta
    taipei = timezone(timedelta(hours=8))
    return datetime.now(taipei).strftime('%Y-%m-%d %H:%M:%S')

def format_taipei_date() -> str:
    from datetime import timezone, timedelta
    taipei = timezone(timedelta(hours=8))
    return datetime.now(taipei).strftime('%Y-%m-%d')
