"""Real PowerShell + loopback tests for instance identity and safe stop guards."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading
import pytest

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows launch scripts')
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def service(tmp_path):
    state={'health':{'service':'ready','name':'DictionForge','workspace':str(ROOT),
                    'data_root':str(tmp_path),'pid':12345},'posts':0,'stop_status':409}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body=json.dumps(state['health']).encode();self.send_response(200)
            self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        def do_POST(self):
            state['posts']+=1;self.send_response(state['stop_status']);self.end_headers();self.wfile.write(b'active task')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield server.server_port,state,tmp_path
    server.shutdown();server.server_close();thread.join()

def run(name,service,*args):
    port,_,data=service
    return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts'/name),
        '-Port',str(port),'-DataRoot',str(data),*args],cwd=data,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)

def test_start_reuses_only_matching_workspace_from_arbitrary_directory(service):
    result=run('start.ps1',service,'-NoBrowser')
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout.strip());assert value['reused'] is True and value['pid']==12345
    assert service[1]['posts']==0

@pytest.mark.parametrize('key,value',[('workspace',r'E:\another-workspace'),('data_root',r'E:\another-data'),('name','Unrelated')])
def test_foreign_instance_never_receives_stop(service,key,value):
    service[1]['health'][key]=value
    assert run('stop.ps1',service).returncode!=0
    assert run('start.ps1',service,'-NoBrowser').returncode!=0
    assert service[1]['posts']==0

def test_active_task_stop_rejection_keeps_service_alive(service):
    result=run('stop.ps1',service)
    assert result.returncode!=0 and service[1]['posts']==1
    assert run('start.ps1',service,'-NoBrowser').returncode==0
