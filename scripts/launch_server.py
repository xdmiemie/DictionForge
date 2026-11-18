"""Detach the owned service from cmd/PowerShell consoles, retaining file logs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stdout',type=Path,required=True)
    parser.add_argument('--stderr',type=Path,required=True)
    args=parser.parse_args()
    flags=(subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP) if os.name=='nt' else 0
    with args.stdout.open('ab') as out,args.stderr.open('ab') as err:
        child=subprocess.Popen([sys.executable,'-m','dictionforge.serve'],cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,stdout=out,stderr=err,close_fds=True,creationflags=flags,
            start_new_session=os.name!='nt')
    print(json.dumps({'pid':child.pid}))

if __name__=='__main__':main()
