import io
import json
import sqlite3
import threading
import time

import pytest

from web.console_log import _STRUCTURAL, console_activity, read_console_events, run_console


def create_events(path, events):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
        db.execute("CREATE INDEX events_session_interaction ON events(json_extract(payload,'$.session'),id) WHERE "+_STRUCTURAL)
        db.executemany('INSERT INTO events VALUES (?,?)',[(index,json.dumps(event,ensure_ascii=False)) for index,event in enumerate(events,1)])


def event(kind,text):return {'type':kind,'data':{'text':text,'msg':text},'ts':time.time()}


def test_tail_finds_structural_history_behind_noisy_terminal_output(tmp_path):
    path=tmp_path/'events.sqlite'
    create_events(path,[event('chat.user','原任务')]+[event('terminal.output','noisy') for _ in range(1100)])
    cursor,lines=read_console_events(path)
    assert cursor==1101 and len(lines)==1 and '原任务' in lines[0]
    assert read_console_events(path,cursor)==(cursor,[])
    with sqlite3.connect(path) as db:assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0]==1101


def test_follow_drains_large_burst_without_losing_last_summary(tmp_path):
    path=tmp_path/'events.sqlite'
    create_events(path,[event('terminal.output','noise') for _ in range(1500)]+[event('system','汇报已保存')])
    cursor,lines=read_console_events(path,0)
    assert cursor==1000 and not lines
    cursor,lines=read_console_events(path,cursor)
    assert cursor==1501 and '汇报已保存' in lines[0]


def test_missing_database_is_never_created_by_viewer(tmp_path):
    with pytest.raises(sqlite3.OperationalError):read_console_events(tmp_path/'events.sqlite')
    assert not list(tmp_path.iterdir())


def test_activity_status_is_read_from_real_attempt_metadata(tmp_path):
    with sqlite3.connect(tmp_path/'terminals.sqlite') as db:
        db.execute('CREATE TABLE attempts(profile TEXT,last_activity REAL,status TEXT,started REAL)')
        db.executemany('INSERT INTO attempts VALUES (?,?,?,?)',[
            ('ags_active',time.time()-3,'running',1),('ags_old',time.time(),'completed',2),('ags_stale',time.time()-172800,'running',3)])
    status=console_activity(tmp_path)
    assert 'ags_active' in status and 'ags_old' not in status and 'ags_stale' not in status


def test_quiet_runtime_still_shows_viewer_status_and_can_close_cleanly(tmp_path):
    create_events(tmp_path/'events.sqlite',[])
    stop=threading.Event();output=io.StringIO()
    thread=threading.Thread(target=run_console,kwargs={'state_dir':tmp_path,'stop_event':stop,'output':output,'poll_interval':.01,'status_interval':.02})
    thread.start()
    try:
        deadline=time.monotonic()+2
        while output.getvalue().count('[状态]')<2 and time.monotonic()<deadline:time.sleep(.01)
    finally:
        stop.set();thread.join(2)
    assert '关闭本窗口只停止看日志' in output.getvalue()
    assert output.getvalue().count('[状态]')>=2 and not thread.is_alive()
