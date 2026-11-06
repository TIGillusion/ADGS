"""Export only graph/A2A events from the real DB, without modifying runtime state.
Run from repository root: python web3/e2e/export-a2a-replay.py
Then run web3/e2e/a2a-replay.mjs against the isolated mock server.
"""
import json
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[2]
connection = sqlite3.connect((root / '.adgs_runtime/events.sqlite').as_uri() + '?mode=ro', uri=True)
sessions = ('a2a-live-test', 'mijia_lab')
result = []
for (payload,) in connection.execute(
        "SELECT payload FROM events WHERE payload LIKE '%a2a-live-test%' OR payload LIKE '%mijia_lab%' ORDER BY id"):
    event = json.loads(payload)
    if event.get('session') in sessions and event.get('type', '').startswith(('graph.', 'node.', 'a2a.', 'chat.')):
        result.append(event)
connection.close()
destination = root / 'web3/test/.a2a-real-events.json'
destination.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
print(f'Exported {len(result)} events to {destination}')
