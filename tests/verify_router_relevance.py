"""Read-only local BGE verification; no worker/model API tasks or registry writes."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('OMP_NUM_THREADS','2')
os.environ.setdefault('MKL_NUM_THREADS','2')
import torch
torch.set_num_threads(2)
from config.config import config
from core.registry import GENERALIST_DESCRIPTION, Node, registry
from core.router import ranked_candidates, routing_details, is_generalist

config.router_sim_weight=3.0
config.router_score_weight=0.2
config.router_explore_beta=.15
descriptions={
    'general':GENERALIST_DESCRIPTION,
    'frontend':'擅长 React 前端开发、TypeScript 和 CSS 布局，能够构建网页界面。',
    'math':'擅长数论、素数研究、数学定理证明、公式推导与独立验证。',
    'device':'精通米家智能家居设备控制、物联网协议与硬件调试。',
    'novel':'擅长网络小说创作、人物塑造、剧情设计与文学文本编辑。',
}
tasks=[
    ('frontend','使用 React 和 TypeScript 构建网页界面并编写前端测试'),
    ('math','研究数论猜想并证明素数的模运算恒等式'),
    ('device','设计物联网硬件电路并控制米家插座'),
    ('novel','撰写长篇小说大纲并优化人物情节'),
    ('general','研究食谱、营养和烹饪方法'),
]
report=[]
for expected,task in tasks:
    nodes=[Node(key,description,score=1 if key==expected else 100000,
                success_rate=.1 if key==expected else 1) for key,description in descriptions.items()]
    for node in nodes:node.tasks_done=0 if node.id=='general' else 100
    details=routing_details(task,nodes)
    chosen=ranked_candidates(task,nodes)[0][0].id
    old=max(nodes,key=lambda n:(details[n.id]['similarity'] or 0)**1.5 * n.success_rate**.5).id
    row={'expected':expected,'old':old,'new':chosen,
         'similarities':{key:round(value['similarity'] or 0,3) for key,value in details.items()}}
    report.append(row);assert chosen==expected,row

real=registry.active()
real_report={}
for label,task in tasks:
    real_report[label]=[{'id':node.id,'similarity':round(fit['similarity'] or 0,3),
                         'fit':round(fit['relevance'],3),'generalist':fit['generalist']}
                       for node,fit in ranked_candidates(task,real)[:5]]
print(json.dumps({'scenarios':report,'real_population':len(real),
                  'real_generalists':sum(is_generalist(n) for n in real),'real_shortlists':real_report},ensure_ascii=True,indent=2))
