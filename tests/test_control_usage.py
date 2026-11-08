import sqlite3

from web.control_usage import session_usage


def database(root, profile, rows):
    path=root/profile/'state.db';path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sessions(id TEXT, model TEXT,input_tokens INTEGER,output_tokens INTEGER,cache_read_tokens INTEGER,cache_write_tokens INTEGER,api_call_count INTEGER)')
        db.executemany('INSERT INTO sessions VALUES (?,?,?,?,?,?,?)',rows)


def test_native_accounting_deduplicates_and_uses_exclusive_input_buckets(tmp_path):
    database(tmp_path,'ceo',[('c','paid',100,20,900,0,2),('unrelated','secret-model',99999,0,99999,0,99)])
    database(tmp_path,'ags_1',[('w','local',100,10,0,100,3)])
    r=session_usage(tmp_path,[('ceo','c','ceo'),('ceo','c','ceo'),('ags_1','w','worker')])
    assert r['totals']['native_session_count']==2 and r['totals']['total_input_tokens']==1200
    assert r['totals']['cache_hit_rate_pct']==75
    assert r['by_role']['ceo']['cache_hit_rate_pct']==90
    assert r['by_role']['worker']['cache_hit_rate_pct']==0
    assert r['totals']['api_call_count']==5 and not r['partial']
    assert 'secret-model' not in str(r)


def test_unknown_usage_is_partial_and_does_not_create_databases(tmp_path):
    database(tmp_path,'ags_1',[('w','local',0,0,0,0,0)])
    r=session_usage(tmp_path,[('ags_1','w','worker'),('ags_1','missing','worker'),('ags_2','missing','worker'),('../outside','x','worker')])
    assert r['partial'] and len(r['unavailable'])==3
    assert r['totals']['cache_hit_rate_pct'] is None
    assert r['totals']['cache_reporting']=='no_cache_tokens_reported'
    assert not (tmp_path/'ags_2').exists()


def test_unreadable_schema_keeps_other_profile_results(tmp_path):
    database(tmp_path,'ags_1',[('w','local',25,0,75,0,1)])
    bad=tmp_path/'ags_2/state.db';bad.parent.mkdir();bad.write_bytes(b'not sqlite')
    r=session_usage(tmp_path,[('ags_1','w','worker'),('ags_2','bad','worker')])
    assert r['partial'] and r['totals']['cache_hit_rate_pct']==75
