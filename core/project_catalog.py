"""Project/session sidebar metadata; execution memory and population remain global."""
import json
import hashlib
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


def session_workspace(data):
    tasks = (data.get('facts') or {}).get('tasks') or {}
    recorded = next((t['workspace'].strip() for t in reversed(list(tasks.values()))
                     if isinstance(t, dict) and isinstance(t.get('workspace'), str) and t['workspace'].strip()), None)
    direct = data.get('workspace')
    return recorded or (direct.strip() if isinstance(direct, str) and direct.strip() else None)


def legacy_session_created_at(session_id, fallback):
    match = re.match(r'^auto-(\d{8}-\d{6})-', session_id)
    if match:
        try:
            return datetime.strptime(match[1], '%Y%m%d-%H%M%S').timestamp()
        except ValueError:
            pass
    return fallback


class ProjectCatalog:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = threading.RLock()

    @contextmanager
    def connect(self):
        path = self.path or Path(os.environ.get('ADGS_STATE_DIR', str(Path(__file__).resolve().parents[1] / '.adgs_runtime'))) / 'projects.sqlite'
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript('''CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, workspace TEXT NOT NULL,
              created_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
              created_at REAL NOT NULL, updated_at REAL NOT NULL,
              archived INTEGER NOT NULL DEFAULT 0,
              completion_seen INTEGER NOT NULL DEFAULT 0);''')
            if 'archived' not in {row['name'] for row in connection.execute('PRAGMA table_info(projects)')}:
                with connection:
                    connection.execute('BEGIN IMMEDIATE')
                    if 'archived' not in {row['name'] for row in connection.execute('PRAGMA table_info(projects)')}:
                        connection.execute('ALTER TABLE projects ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
            if 'completion_seen' not in {row['name'] for row in connection.execute('PRAGMA table_info(sessions)')}:
                with connection:
                    connection.execute('BEGIN IMMEDIATE')
                    if 'completion_seen' not in {row['name'] for row in connection.execute('PRAGMA table_info(sessions)')}:
                        connection.execute('ALTER TABLE sessions ADD COLUMN completion_seen INTEGER NOT NULL DEFAULT 0')
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def label(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
            raise ValueError('名称需要1至120个字符')
        return value.strip()

    def create_project(self, name, workspace):
        name = self.label(name)
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError('请选择项目工作目录')
        directory = Path(workspace).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError('项目工作目录不存在或不是目录')
        item = dict(id=uuid.uuid4().hex, name=name, workspace=str(directory), created_at=time.time(), archived=0)
        with self.lock, self.connect() as db:
            db.execute('INSERT INTO projects (id,name,workspace,created_at,archived) VALUES (:id,:name,:workspace,:created_at,:archived)', item)
        return item

    def project(self, project_id):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
            return dict(row) if row else None

    def session(self, session_id):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT s.*,p.name AS project_name,p.workspace,p.archived AS project_archived FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.id=?', (session_id,)).fetchone()
            return dict(row) if row else None

    def create_session(self, project_id, title='新会话'):
        now = time.time()
        item = dict(id='chat-'+uuid.uuid4().hex, project_id=project_id, title=self.label(title), created_at=now, updated_at=now, archived=0)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = db.execute('SELECT archived FROM projects WHERE id=?', (project_id,)).fetchone()
            if project is None:
                raise KeyError('项目不存在')
            if project['archived']:
                raise ValueError('请先恢复已归档的项目')
            db.execute('INSERT INTO sessions (id,project_id,title,created_at,updated_at,archived,completion_seen) VALUES (:id,:project_id,:title,:created_at,:updated_at,:archived,0)', item)
        return self.session(item['id'])

    def discard_new_session(self, session_id):
        with self.lock, self.connect() as db:
            db.execute('DELETE FROM sessions WHERE id=?', (session_id,))

    def update_project(self, project_id, name=None, archived=None):
        if name is not None:
            name = self.label(name)
        if archived is not None and type(archived) is not bool:
            raise ValueError('archived 必须是布尔值')
        if name is None and archived is None:
            raise ValueError('请提供项目名称或归档状态')
        with self.lock, self.connect() as db:
            if not db.execute('UPDATE projects SET name=COALESCE(?,name), archived=COALESCE(?,archived) WHERE id=?',
                              (name, archived, project_id)).rowcount:
                raise KeyError('项目不存在')
        return self.project(project_id)

    def update_session(self, session_id, title=None, archived=None, completion_seen=None):
        if title is not None:
            title = self.label(title)
        if archived is not None and type(archived) is not bool:
            raise ValueError('archived 必须是布尔值')
        if completion_seen is not None and type(completion_seen) is not bool:
            raise ValueError('completion_seen 必须是布尔值')
        if title is None and archived is None and completion_seen is None:
            raise ValueError('请提供会话名称、归档状态或查看状态')
        with self.lock, self.connect() as db:
            if not db.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone():
                raise KeyError('会话不存在')
            if title is not None:
                db.execute('UPDATE sessions SET title=?,updated_at=? WHERE id=?', (title,time.time(),session_id))
            if archived is not None:
                db.execute('UPDATE sessions SET archived=?,updated_at=? WHERE id=?', (int(archived),time.time(),session_id))
            if completion_seen is not None:
                db.execute('UPDATE sessions SET completion_seen=? WHERE id=?', (int(completion_seen),session_id))
        return self.session(session_id)

    def discover(self, directory, workspace):
        """Import references to old files once; never rewrite their histories."""
        directory = Path(directory)
        with self.lock, self.connect() as db:
            if directory.exists():
                for path in directory.glob('*.json'):
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', path.stem):
                        continue
                    if db.execute('SELECT 1 FROM sessions WHERE id=?',(path.stem,)).fetchone():
                        continue
                    try:
                        data=json.loads(path.read_text(encoding='utf-8'))
                        first=next((str(m.get('content','')).strip() for m in data.get('history',[]) if m.get('role')=='user' and str(m.get('content','')).strip() and not str(m.get('content','')).lstrip().startswith('[')),path.stem)
                        recorded = session_workspace(data)
                        directory_path = str(Path(recorded or workspace).resolve())
                        existing = db.execute('SELECT id FROM projects WHERE workspace=? AND id<>?',(directory_path,'legacy-unknown')).fetchone() if recorded else None
                        project_id = existing['id'] if existing else ('legacy-unknown' if not recorded else 'legacy-'+hashlib.sha256(directory_path.encode()).hexdigest()[:16])
                        label = (Path(directory_path).name or directory_path) if recorded else '未记录工作区'
                        stamp=path.stat().st_mtime
                        created = legacy_session_created_at(path.stem, stamp)
                        db.execute('INSERT OR IGNORE INTO projects (id,name,workspace,created_at) VALUES (?,?,?,?)',
                                   (project_id,label,directory_path,created))
                        db.execute('INSERT OR IGNORE INTO sessions (id,project_id,title,created_at,updated_at,archived,completion_seen) VALUES (?,?,?,?,?,0,0)',(path.stem,project_id,first[:80] or path.stem,created,stamp))

                    except (ValueError,OSError,TypeError,AttributeError):
                        continue

    def listing(self, include_archived=False):
        with self.lock, self.connect() as db:
            projects = {row['id']: {**dict(row), 'sessions': []} for row in db.execute('SELECT * FROM projects')}
            sessions = [dict(row) for row in db.execute('SELECT * FROM sessions ORDER BY updated_at DESC,id')]
        legacy_dates = {}
        for session in sessions:
            project = projects.get(session['project_id'])
            if project is None:
                continue
            if project['id'].startswith('legacy-'):
                created = legacy_session_created_at(session['id'], session['created_at'])
                legacy_dates[project['id']] = min(legacy_dates.get(project['id'], created), created)
            if include_archived or not session['archived']:
                project['sessions'].append(session)
        for project_id, created in legacy_dates.items():
            projects[project_id]['created_at'] = created
        visible = (project for project in projects.values() if include_archived or not project['archived'])
        return sorted(visible, key=lambda project: (-project['created_at'], project['id']))

    def touch(self, session_id, text):
        with self.lock, self.connect() as db:
            db.execute("UPDATE sessions SET updated_at=?, completion_seen=0, title=CASE WHEN title='新会话' THEN ? ELSE title END WHERE id=?",(time.time(),text[:80],session_id))


project_catalog = ProjectCatalog()
