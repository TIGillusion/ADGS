import { useEffect, useState, type ReactNode } from "react";
import { Activity, Archive, ArchiveRestore, Check, ChevronDown, ChevronRight, Folder, FolderOpen, FolderPlus, Info, MessageSquare, MoreHorizontal, Network, Pencil, Plus, Search, Share2, X } from "lucide-react";
import { store, useAppState } from "@/lib/store";
import type { ViewId } from "@/types";

type Editor = { kind: 'project' | 'project-name' | 'chat-name'; id?: string; name: string; workspace: string };
const TERMINAL_BADGES = {
  completed: ['成', '已完成'],
  blocked: ['阻', '任务受阻'],
  failed: ['败', '任务失败'],
  interrupted: ['断', '任务中断'],
  exception: ['错', '执行异常'],
  limit: ['限', '达到决策上限'],
  paused: ['停', '任务暂停'],
} as const;
export function ProjectSidebar({footer,onInspector,onNavigate}: {footer:ReactNode;onInspector:()=>void;onNavigate?:()=>void}) {
  const projects=useAppState(s=>s.projects),supported=useAppState(s=>s.projectSupport);
  const selected=useAppState(s=>s.viewSession),view=useAppState(s=>s.view),snap=useAppState(s=>s.snap);
  const [closed,setClosed]=useState<Set<string>>(() => { try { const value: unknown = JSON.parse(localStorage.getItem('adgs_closed_projects') || '[]'); return new Set(Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []); } catch { return new Set(); } });
  const [showArchive, setShowArchive] = useState(false);
  const [query,setQuery]=useState(''),[editor,setEditor]=useState<Editor|null>(null),[saving,setSaving]=useState(false),[menu,setMenu]=useState<string|null>(null);
  useEffect(() => { try { localStorage.setItem('adgs_closed_projects', JSON.stringify([...closed])); } catch { /* storage unavailable */ } }, [closed]);
  useEffect(() => { const close = (e: KeyboardEvent) => { if(e.key === 'Escape') setMenu(null); }; window.addEventListener('keydown',close); return () => window.removeEventListener('keydown',close); }, []);
  const activeProjects = projects.filter(project => !project.archived);
  const current = activeProjects.find(project => project.sessions.some(chat => chat.id === selected && !chat.archived));
  const archivedCount = projects.reduce((count, project) =>
    count + (project.archived ? 1 : project.sessions.filter(chat => chat.archived).length), 0);
  const normalize = (value: string) => value.trim().toLowerCase().replace(/\\/g, "/");
  const term = normalize(query);
  const visibleProjects = [...projects].sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0)).flatMap(project => {
    if (!showArchive && project.archived) return [];
    const inScope = project.sessions.filter(chat => showArchive === Boolean(project.archived || chat.archived));
    if (showArchive && !project.archived && !inScope.length) return [];
    const projectMatches = normalize(project.name).includes(term) || normalize(project.workspace).includes(term);
    const chats = projectMatches ? inScope : inScope.filter(chat => normalize(chat.title).includes(term));
    return projectMatches || chats.length ? [{ project, chats }] : [];
  });
  const run=async(action:()=>Promise<void>)=>{try{await action();}catch(e){store.toast('error',e instanceof Error?e.message:String(e));}};
  const newChat=()=>{const project=current??activeProjects[0];setShowArchive(false);setQuery('');if(project)void run(()=>store.createProjectSession(project.id));else setEditor({kind:'project',name:'',workspace:snap?.workspace||''});};
  const nav:{id:ViewId;label:string;icon:ReactNode}[]=[{id:'chat',label:'对话工作台',icon:<MessageSquare size={19}/>},{id:'graph',label:'演化图谱',icon:<Share2 size={19}/>},{id:'nodes',label:'节点种群',icon:<Network size={19}/>},{id:'overview',label:'系统概览',icon:<Activity size={19}/>}];
  return <>
    <aside className="app-rail">
      <button className="rail-brand" title="ADGS" onClick={()=>store.setView('chat')}><span>A</span></button>
      <nav aria-label="工作台导航">{nav.map(n=><button key={n.id} aria-label={n.label} title={n.label} className={view===n.id?'active':''} onClick={()=>{store.setView(n.id);onNavigate?.();}}>{n.icon}<span className="sr-only">{n.label}</span></button>)}</nav>
      <button className="rail-info" aria-label="显示或隐藏项目资料" title="项目资料" onClick={onInspector}><Info size={18}/></button>
      <div className="rail-avatar" title="全局共享节点种群">群</div>
    </aside>
    <aside className="project-sidebar" id="project-navigation" aria-label="项目和会话">
      <div className="project-sidebar-heading"><strong>ADGS <span>Workspace</span></strong><button aria-label="新建项目" title="新建项目" disabled={!supported} onClick={()=>setEditor({kind:'project',name:'',workspace:snap?.workspace||''})}><FolderPlus size={18}/></button></div>
      <button className="new-chat" disabled={!supported} onClick={newChat}><Pencil size={17}/><span>新会话</span><Plus size={15}/></button>
      <div className="project-search"><Search size={15} aria-hidden="true"/>
        <input type="text" aria-label="搜索项目和会话" placeholder="搜索项目和会话" value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if(e.key==='Escape')setQuery('');}}/>
        {query && <button aria-label="清除搜索" title="清除搜索" onClick={()=>setQuery('')}><X size={14}/></button>}
      </div>
      <div className="project-scope" role="group" aria-label="项目列表范围">
        <button aria-pressed={!showArchive} onClick={()=>{setShowArchive(false);setMenu(null);}}><Folder size={13}/>工作区 <span>{activeProjects.length}</span></button>
        {supported && <button aria-pressed={showArchive} onClick={()=>{setShowArchive(true);setMenu(null);}}><Archive size={13}/>已归档 <span>{archivedCount}</span></button>}
      </div>
      <div className="project-list">
        <div className="project-list-caption">{term ? `搜索结果 · ${visibleProjects.length} 个工作区` : showArchive ? "归档记录" : "工作区 · 按创建时间排序"}</div>
        {!visibleProjects.length && <div className="project-empty" role="status">{term ? "没有匹配的项目或会话" : showArchive ? "暂无归档项目或会话" : "暂无项目"}</div>}
        {visibleProjects.map(({project:p, chats})=>{
          const collapsed=closed.has(p.id)&&!term;
          const busy = p.sessions.some(chat => chat.busy);
          return <section className="project-group" key={p.id}>
            <div className="project-group-heading">
              <button className="project-toggle" title={p.workspace} onClick={()=>setClosed(old=>{const next=new Set(old);if(next.has(p.id))next.delete(p.id);else next.add(p.id);return next;})} aria-expanded={!collapsed}>
                {collapsed?<ChevronRight size={13}/>:<ChevronDown size={13}/>} {collapsed?<Folder size={17}/>:<FolderOpen size={17}/>}<span>{p.name}</span>
              </button>
              {supported && !showArchive && <button aria-label={`在${p.name}新建会话`} title="新建会话" onClick={()=>void run(()=>store.createProjectSession(p.id))}><Plus size={14}/></button>}
              {supported && (!showArchive || Boolean(p.archived)) && <button aria-label={`管理项目${p.name}`} title="管理项目" aria-expanded={menu===p.id} onClick={()=>setMenu(menu===p.id?null:p.id)}><MoreHorizontal size={15}/></button>}
            </div>
            {menu===p.id && <div className="chat-actions">
              {!p.archived && <button onClick={()=>{setEditor({kind:'project-name',id:p.id,name:p.name,workspace:p.workspace});setMenu(null);}}><Pencil size={12}/>重命名</button>}
              <button disabled={busy && !p.archived} title={busy && !p.archived ? "请等待项目中的会话结束" : undefined} onClick={()=>{setMenu(null);void run(()=>store.setProjectArchived(p.id,!p.archived));}}>
                {p.archived ? <><ArchiveRestore size={12}/>恢复项目</> : <><Archive size={12}/>归档项目</>}
              </button>
            </div>}
            {!collapsed&&<div className="project-chats">{chats.map(c=><div key={c.id}>
              <div data-session-id={c.id} className={`project-chat ${selected===c.id?'selected':''}`}>
                <button className="project-chat-open" title={c.title} onClick={()=>{store.setView('chat');void store.loadSession(c.id);onNavigate?.();}}><span>{c.title}</span>{c.busy&&<span className="session-busy" title="正在运行">忙</span>}{!c.busy&&c.terminal_status&&!c.completion_seen&&<span className={`session-status session-status-${c.terminal_status}`} title={`${TERMINAL_BADGES[c.terminal_status]?.[1] ?? '任务已结束'}，打开后标记为已读`}>{TERMINAL_BADGES[c.terminal_status]?.[0] ?? '结'}</span>}</button>
                {supported && !p.archived && <button className="chat-menu-trigger" aria-label={`管理会话${c.title}`} aria-expanded={menu===c.id} onClick={()=>setMenu(menu===c.id?null:c.id)}><MoreHorizontal size={15}/></button>}
              </div>
              {!p.archived&&menu===c.id&&<div className="chat-actions">
                {!c.archived && <button onClick={()=>{setEditor({kind:'chat-name',id:c.id,name:c.title,workspace:p.workspace});setMenu(null);}}><Pencil size={12}/>重命名</button>}
                <button disabled={c.busy && !c.archived} title={c.busy ? "请等待会话结束" : undefined} onClick={()=>{setMenu(null);void run(()=>store.setSessionArchived(c.id,!c.archived));}}>
                  {c.archived ? <><ArchiveRestore size={12}/>恢复会话</> : <><Archive size={12}/>归档会话</>}
                </button>
              </div>}
            </div>)}{!chats.length&&<div className="no-chats">尚无会话</div>}</div>}
          </section>;
        })}
      </div>
      <div className="sidebar-global"><Network size={15}/><span>全局群体</span><strong>{snap?.population?.active??'—'}</strong><span>个节点</span></div>
      <div className="project-sidebar-footer">{footer}</div>
    </aside>
    {editor&&<div className="project-modal-backdrop" onPointerDown={e=>{if(e.target===e.currentTarget&&!saving)setEditor(null);}}>
      <form onKeyDown={e=>{if(e.key==='Escape'&&!saving)setEditor(null);}} role="dialog" aria-modal="true" aria-label={editor.kind==='project'?'新建项目':'重命名'} className="project-modal" onSubmit={async e=>{
        e.preventDefault();
        setSaving(true);
        try {
          if(editor.kind==='project') {
            await store.createProject(editor.name,editor.workspace);
            setShowArchive(false);
            setQuery('');
          } else if(editor.kind==='project-name') await store.renameProject(editor.id!,editor.name);
          else await store.renameSession(editor.id!,editor.name);
          setEditor(null);
        } catch(error) {
          store.toast('error',error instanceof Error?error.message:String(error));
        } finally {setSaving(false);}
      }}>
        <div className="project-modal-title"><Folder size={20}/><h2>{editor.kind==='project'?'新建项目':editor.kind==='chat-name'?'重命名会话':'重命名项目'}</h2><button type="button" aria-label="关闭" onClick={()=>setEditor(null)}><X size={17}/></button></div>
        <label>名称<input autoFocus required maxLength={120} value={editor.name} onChange={e=>setEditor({...editor,name:e.target.value})}/></label>
        {editor.kind==='project'&&<label>工作目录<input required placeholder="D:\\projects\\my-project" value={editor.workspace} onChange={e=>setEditor({...editor,workspace:e.target.value})}/><small>此项目中的会话使用同一工作目录。</small></label>}
        <div className="project-modal-actions"><button type="button" onClick={()=>setEditor(null)}>取消</button><button className="primary" type="submit" disabled={saving||!editor.name.trim()}>{saving?'保存中…':<><Check size={14}/> {editor.kind==='project'?'创建项目':'保存'}</>}</button></div>
      </form>
    </div>}
  </>;
}

export function ProjectInspector({onClose}:{onClose:()=>void}) {
  const projects=useAppState(s=>s.projects),selected=useAppState(s=>s.viewSession),snap=useAppState(s=>s.snap);
  const project=projects.find(p=>p.sessions.some(c=>c.id===selected));
  const chat=project?.sessions.find(c=>c.id===selected);
  return <aside className="project-inspector">
    <div className="inspector-card"><div className="inspector-title"><Folder size={16}/><span>{project?.name||'项目资料'}</span><button aria-label="收起项目资料" onClick={onClose}><X size={15}/></button></div>
      <h3>工作目录</h3><div className="inspector-path">{project ? project.workspace || '历史记录未保存工作目录' : snap?.workspace || '尚未选择项目'}</div>
      <div className="inspector-rule"/><h3>当前会话</h3><p>{chat?.title||'选择一个会话，开始工作'}</p>
      <div className="inspector-channels"><button onClick={()=>store.setView('graph')}><Share2 size={15}/>演化图谱<ChevronRight size={13}/></button><button onClick={()=>store.setView('nodes')}><Network size={15}/>全局节点种群<ChevronRight size={13}/></button></div>
    </div>
    <div className="inspector-footnote"><span className="dot" style={{background:'var(--life-400)'}}/>全部项目共享长期记忆、技能和进化成果</div>
  </aside>;
}
