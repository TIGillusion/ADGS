"""Read-only native Hermes accounting scoped to one ADGS conversation."""
import re
import sqlite3
from pathlib import Path

_PROFILE = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
_COUNTERS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'api_call_count')


def session_usage(profiles_dir, references):
    rows, unavailable, groups = [], [], {}
    # Continuations/attempts share cumulative counters: deduplicate native IDs.
    for profile, sid, role in set(references):
        if not isinstance(profile, str) or not _PROFILE.fullmatch(profile):
            unavailable.append({'profile': profile, 'session_id': sid, 'reason': 'invalid_profile'})
            continue
        groups.setdefault(profile, {})[sid] = role
    root = Path(profiles_dir).resolve()
    for profile, sessions in groups.items():
        path = (root / profile / 'state.db').resolve()
        if not path.is_relative_to(root) or not path.is_file():
            unavailable.extend({'profile': profile, 'session_id': sid, 'reason': 'database_unavailable'} for sid in sessions)
            continue
        try:
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3) as db:
                db.row_factory = sqlite3.Row
                for sid, role in sessions.items():
                    value = db.execute('SELECT id,model,' + ','.join(_COUNTERS) + ' FROM sessions WHERE id=?', (sid,)).fetchone()
                    if value is None:
                        unavailable.append({'profile': profile, 'session_id': sid, 'reason': 'session_not_found'})
                        continue
                    rows.append({'profile': profile, 'session_id': sid, 'role': role, 'model': value['model'],
                                 **{key: max(0, int(value[key] or 0)) for key in _COUNTERS}})
        except (OSError, sqlite3.Error, ValueError, TypeError):
            unavailable.extend({'profile': profile, 'session_id': sid, 'reason': 'usage_unavailable'} for sid in sessions
                               if not any(r['profile'] == profile and r['session_id'] == sid for r in rows))

    def aggregate(values):
        result = {key: sum(row[key] for row in values) for key in _COUNTERS}
        result['native_session_count'] = len(values)
        result['total_input_tokens'] = sum(result[k] for k in ('input_tokens', 'cache_read_tokens', 'cache_write_tokens'))
        result['cache_hit_rate_pct'] = (round(100 * result['cache_read_tokens'] / result['total_input_tokens'], 2)
                                        if result['total_input_tokens'] else None)
        result['cache_reporting'] = 'cache_observed' if result['cache_read_tokens'] else 'no_cache_tokens_reported'
        return result
    return {'totals': aggregate(rows), 'by_role': {role: aggregate([r for r in rows if r['role'] == role])
                                                 for role in ('ceo', 'worker')},
            'native_sessions': sorted(rows, key=lambda r: (r['profile'], r['session_id'])),
            'partial': bool(unavailable), 'unavailable': unavailable,
            'scope': 'Known Hermes sessions only; independent routing and acceptance calls are excluded.',
            'cache_note': 'input_tokens excludes cache reads/writes. Zero reported cache tokens may mean the provider does not expose cache usage.'}
