import type { ProjectItem, SessionListItem } from '@/types';

/** Old runtimes expose chats but have no project API. Keep them in the same sidebar. */
export function groupLegacySessions(chats: SessionListItem[], workspaces: Record<string,string> = {}): ProjectItem[] {
  const groups = new Map<string,ProjectItem>();
  for (const chat of chats) {
    const raw = (chat.workspace || workspaces[chat.name] || '').trim();
    const workspace = raw === '/' || /^[A-Za-z]:[\\/]$/.test(raw) ? raw : raw.replace(/[\\/]+$/, '');
    const identity = /^[A-Za-z]:[\\/]/.test(workspace) ? workspace.replace(/\\/g,'/').toLowerCase() : workspace;
    const id = `legacy:${identity || 'unknown'}`;
    let group = groups.get(id);
    if (!group) {
      group = {id, name: workspace.split(/[\\/]/).filter(Boolean).at(-1) || '未记录工作区', workspace, sessions:[]};
      groups.set(id,group);
    }
    const stamp = Date.parse(chat.saved_at.replace(' ', 'T')) / 1000 || 0;
    const auto = /^auto-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-/.exec(chat.name);
    const created = auto ? Date.parse(`${auto[1]}-${auto[2]}-${auto[3]}T${auto[4]}:${auto[5]}:${auto[6]}`) / 1000 || stamp : stamp;
    group.created_at = Math.min(group.created_at ?? created, created);
    group.sessions.push({id:chat.name,project_id:id,title:chat.preview?.trim() || chat.name,created_at:created,updated_at:stamp,
      completed: chat.completed, completion_seen: chat.completion_seen, terminal_status: chat.terminal_status});
  }
  return [...groups.values()];
}
