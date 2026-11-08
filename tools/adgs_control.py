"""ADGS owner API client using only the Python standard library."""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote,urlencode
from urllib.request import Request,urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]


class ControlClient:
    def __init__(self,url=None,token=None,timeout=45):
        self.url=(url or os.environ.get('ADGS_CONTROL_URL','http://127.0.0.1:8021/api/v1')).rstrip('/')
        state=Path(os.environ.get('ADGS_STATE_DIR',str(ROOT/'.adgs_runtime')))
        self.token=token or os.environ.get('ADGS_CONTROL_TOKEN') or (state/'control_token').read_text(encoding='utf8').strip()
        self.timeout=timeout

    def request(self,method,path,body=None,key=None):
        if not path.startswith('/') or path.startswith('//') or '://' in path:
            raise ValueError('Use an API-relative path starting with /')
        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'}
        if key:headers['Idempotency-Key']=key
        data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
        request=Request(self.url+path,data=data,headers=headers,method=method)
        with urlopen(request,timeout=self.timeout) as response:
            return json.load(response)


def main():
    parser=argparse.ArgumentParser(description='ADGS external AI control client')
    parser.add_argument('--url');parser.add_argument('--timeout',type=int,default=45)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('status');commands.add_parser('sessions')
    inspect=commands.add_parser('inspect');inspect.add_argument('session')
    for name in ('state','usage'):
        command=commands.add_parser(name);command.add_argument('session')
    resume=commands.add_parser('resume');resume.add_argument('session');resume.add_argument('--execution',required=True);resume.add_argument('--instruction');resume.add_argument('--key',required=True)
    message=commands.add_parser('message');message.add_argument('session');message.add_argument('text');message.add_argument('--mode',choices=['queue','interrupt'],default='queue');message.add_argument('--key',required=True)
    events=commands.add_parser('events');events.add_argument('--session');events.add_argument('--after',type=int,default=0);events.add_argument('--limit',type=int,default=100)
    request=commands.add_parser('request');request.add_argument('method');request.add_argument('path');request.add_argument('--body',help='JSON text or @JSON-file');request.add_argument('--key')
    args=parser.parse_args();client=ControlClient(args.url,timeout=args.timeout)
    if args.command=='status':result=client.request('GET','/health')
    elif args.command=='sessions':result=client.request('GET','/sessions')
    elif args.command=='inspect':result=client.request('GET','/sessions/'+quote(args.session,safe=''))
    elif args.command in ('state','usage'):result=client.request('GET','/sessions/'+quote(args.session,safe='')+'/'+args.command)
    elif args.command=='resume':
        body={'execution_id':args.execution}
        if args.instruction:body['instruction']=args.instruction
        result=client.request('POST','/sessions/'+quote(args.session,safe='')+'/resume',body,args.key)
    elif args.command=='message':result=client.request('POST','/sessions/'+quote(args.session,safe='')+'/messages',{'text':args.text,'mode':args.mode},args.key)
    elif args.command=='events':result=client.request('GET','/events?'+urlencode({k:v for k,v in {'after':args.after,'limit':args.limit,'session':args.session}.items() if v is not None}))
    else:
        body=json.loads(Path(args.body[1:]).read_text(encoding='utf8') if args.body.startswith('@') else args.body) if args.body else None
        result=client.request(args.method.upper(),args.path,body,args.key)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except HTTPError as error:
        print(error.read().decode('utf8',errors='replace'),file=sys.stderr)
        sys.exit(1)
