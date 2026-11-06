import { describe, expect, it } from 'vitest';
import { groupLegacySessions } from '@/lib/legacyProjects';

describe('历史工作区分组',()=>{
  it('同一 Windows 目录的大小写和斜杠差异归入同组，没有目录的保持未知',()=>{
    const chats = ['a','b','c','d'].map(name=>({name,preview:`任务${name}`,saved_at:'2026-11-03 12:00:00',msg_count:1}));
    const groups=groupLegacySessions(chats,{a:'D:\\work\\A\\',b:'d:/work/A',c:'D:\\work\\B'});
    expect(groups).toHaveLength(3);
    expect(groups[0].sessions.map(s=>s.id)).toEqual(['a','b']);
    expect(groups[1].name).toBe('B');
    expect(groups[2].name).toBe('未记录工作区');
    expect(groups[2].workspace).toBe('');
    expect(groups[0].sessions[0].title).toBe('任务a');
  });
  it('接口提供的目录优先于兼容索引，POSIX 大小写保持不同',()=>{
    const groups=groupLegacySessions([
      {name:'a',preview:'',workspace:'/work/A',saved_at:'',msg_count:0},
      {name:'b',preview:'B',workspace:'/work/a',saved_at:'',msg_count:0},
    ],{a:'/outdated'});
    expect(groups.map(p=>p.workspace)).toEqual(['/work/A','/work/a']);
    expect(groups[0].sessions[0].title).toBe('a');
  });
});
