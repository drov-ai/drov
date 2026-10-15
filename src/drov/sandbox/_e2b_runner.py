"""Trusted root control programs for a disposable E2B VM, never a public shell tool."""

SETUP = r"""
import json,os,pathlib,subprocess
os.umask(0o077)
for path,size in [('/work',67108864),('/tmp',16777216),('/var/tmp',16777216),('/dev/shm',16777216)]:
    subprocess.run(['mount','-t','tmpfs','-o',f'size={size},nosuid,nodev,noexec','tmpfs',path],check=True,capture_output=True)
    os.chmod(path,0o1777)
os.chmod('/work',0o755)
os.mkdir('/work/inputs',0o755);os.chown('/work/inputs',65534,65534)
os.mkdir('/work/outputs',0o755);os.chown('/work/outputs',65533,65533)
os.mkdir('/opt/drov-job',0o700)
import importlib.metadata
v=os.statvfs('/work')
print(json.dumps({'uid':os.geteuid(),'workspace_bytes':v.f_blocks*v.f_frsize,
 'cpu_count':os.cpu_count(),
 'packages':{n:importlib.metadata.version(n) for n in ['numpy','pandas','matplotlib']}}))
"""

# The root monitor owns pipes, source/config and result. User code runs under a
# distinct UID with no supplementary groups, no-new-privileges and kernel rlimits.
# It cannot forge the root monitor's exit/result or mutate the input ancestor.
RUN = r"""
import base64,ctypes,hashlib,json,math,os,pathlib,resource,selectors,signal,subprocess,sys,time
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(4,0,0,0,0)!=0:os._exit(125)
spec=json.loads(pathlib.Path('/opt/drov-job/request.json').read_bytes())
spec['seconds']=min(spec['seconds'],float(sys.argv[1]))
os.umask(0o077)
for item in spec['inputs']:
    path=pathlib.Path('/work/inputs',item['id']);path.mkdir(mode=0o755)
    data=base64.b64decode(item['data'],validate=True)
    if hashlib.sha256(data).hexdigest()!=item['sha256']:os._exit(125)
    f=path/item['name'];f.write_bytes(data);f.chmod(0o444);os.chown(f,65534,65534)
    path.chmod(0o555);os.chown(path,65534,65534)
os.chmod('/work/inputs',0o555)
def targets():
    active=[]
    for n in os.listdir('/proc'):
        if not n.isdigit():continue
        try:
            rows=pathlib.Path('/proc',n,'status').read_text().splitlines()
            uids=next(r for r in rows if r.startswith('Uid:')).split()[1:]
            state=next(r for r in rows if r.startswith('State:')).split()[1]
            if all(x=='65533' for x in uids) and state not in ('Z','X'):active.append(int(n))
        except FileNotFoundError:pass
    return active
# A second control start cannot re-execute code even if a host bug bypasses the lease.
fd=os.open('/opt/drov-job/started',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
def child():
    os.setgroups([]);os.setgid(65533);os.setuid(65533)
    if libc.prctl(38,1,0,0,0)!=0:os._exit(125)
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_NPROC,(64,64))
    resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
    resource.setrlimit(resource.RLIMIT_AS,(536870912,536870912))
    resource.setrlimit(resource.RLIMIT_FSIZE,(8388608,8388608))
    cpu=max(1,math.ceil(spec['seconds']));resource.setrlimit(resource.RLIMIT_CPU,(cpu,cpu))
    os.umask(0o077)
process=subprocess.Popen([sys.executable,'-I','-B','-c',spec['code']],cwd='/work',
 env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','HOME':'/tmp','TMPDIR':'/tmp',
 'MPLBACKEND':'Agg','MPLCONFIGDIR':'/tmp/matplotlib','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'},
 stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,preexec_fn=child,start_new_session=True)
selector=selectors.DefaultSelector();chunks={'stdout':bytearray(),'stderr':bytearray()}
for name,pipe in [('stdout',process.stdout),('stderr',process.stderr)]:
    os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,name)
termination='exited';truncated={'stdout':False,'stderr':False};total=0;end=time.monotonic()+spec['seconds']
try:
    while selector.get_map():
        if process.poll() is not None:
            for pid in targets():
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass
        if time.monotonic()>=end:termination='timeout';break
        for key,event in selector.select(min(0.05,max(0,end-time.monotonic()))):
            raw=os.read(key.fd,65536)
            if not raw:selector.unregister(key.fileobj);continue
            keep=min(len(raw),spec['max_output_bytes']-total)
            chunks[key.data].extend(raw[:keep]);total+=keep
            if keep<len(raw):truncated[key.data]=True;termination='output_limit';break
        if termination!='exited':break
    if termination=='exited':process.wait(timeout=max(0.001,end-time.monotonic()))
except subprocess.TimeoutExpired:termination='timeout'
finally:
    # Covers escaped sessions and descendants inheriting pipes. Killing the
    # command connection alone is never a cleanup acknowledgement.
    stop=time.monotonic()+5;empty=0
    while empty<2:
        active=targets()
        if not active:empty+=1
        else:
            empty=0
            for sig in (signal.SIGSTOP,signal.SIGKILL):
                for pid in active:
                    try:os.kill(pid,sig)
                    except ProcessLookupError:pass
        if time.monotonic()>stop:os._exit(125)
        time.sleep(0.01)
    process.wait(timeout=1)
    selector.close();process.stdout.close();process.stderr.close()
result={'exit_code':process.returncode if process.returncode>=0 else 128-process.returncode,
 'termination':termination,'stdout':chunks['stdout'].decode('utf-8',errors='replace'),
 'stderr':chunks['stderr'].decode('utf-8',errors='replace'),
 'stdout_truncated':truncated['stdout'],'stderr_truncated':truncated['stderr']}
print(json.dumps(result,separators=(',',':')))
"""
