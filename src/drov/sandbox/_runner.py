"""Fixed trusted Linux programs. User code is only a child process inside the container."""

# PID 1 never shares its UID or writable control files with workload code. It
# also reaps orphaned children after the protected workload wrapper exits.
SUPERVISOR = r"""
import json,os,pathlib,signal,time
os.umask(0o077)
os.mkdir('/work/outputs',0o777)
os.chmod('/work/outputs',0o777)
def reap(*args):
    while True:
        try:
            pid,status=os.waitpid(-1,os.WNOHANG)
            if not pid: break
        except ChildProcessError: break
signal.signal(signal.SIGCHLD,reap)
status=pathlib.Path('/proc/self/status').read_text().splitlines()
data={k:next(x.split(':',1)[1].strip() for x in status if x.startswith(k+':'))
      for k in ['Seccomp','NoNewPrivs','CapEff']}
for name in ['memory.max','memory.swap.max','cpu.max','pids.max']:
    data[name]=pathlib.Path('/sys/fs/cgroup',name).read_text().strip()
data.update(uid=os.geteuid(),root_readonly=bool(os.statvfs('/').f_flag & os.ST_RDONLY))
with open('/work/.drov-control.json','w') as stream:
    json.dump(data,stream);stream.flush();os.fsync(stream.fileno())
while True: time.sleep(1)
"""

# Dumpability is disabled BEFORE creating the untrusted child. Even on a host
# with permissive Yama defaults, the child cannot replace the trusted parent's
# memory or obtain its private descriptors through /proc. Killing the wrapper
# yields a nonzero actual Engine exec exit, never a successful export.
WORKLOAD = r"""
import ctypes,os,pathlib,signal,subprocess,sys,time
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(4,0,0,0,0)!=0 or libc.prctl(3,0,0,0,0)!=0:
    os._exit(125)
child=subprocess.Popen([sys.executable,'-I','-B','-c',sys.argv[1]],close_fds=True)
result=child.wait()
def targets():
    result=[]
    for name in os.listdir('/proc'):
        if not name.isdigit() or int(name)==os.getpid(): continue
        try:
            rows=pathlib.Path('/proc',name,'status').read_text().splitlines()
            uid=next(x for x in rows if x.startswith('Uid:')).split()[1:]
            state=next(x for x in rows if x.startswith('State:')).split()[1]
            if all(x=='65533' for x in uid) and state not in ('Z','X'):
                result.append(int(name))
        except FileNotFoundError: pass
    return result
empty=0
while empty<2:
    active=targets()
    if not active: empty+=1
    else:
        empty=0
        for sig in (signal.SIGSTOP,signal.SIGKILL):
            for pid in active:
                try: os.kill(pid,sig)
                except ProcessLookupError: pass
    time.sleep(0.005)
os._exit(result if result>=0 else 128-result)
"""

# Executed under the supervisor UID only after the workload exec has ended.
# No path extraction occurs on the host. Dirfd traversal and inode checks bound
# every declared ordinary file; hardlinks, links and special files are rejected.
EXPORT = r"""
import base64,hashlib,json,os,stat,sys
spec=json.loads(sys.argv[1]);files=[];total=0
def identity(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
root=os.open('/work/outputs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
    for name in spec['names']:
        parent=os.dup(root);opened=[]
        try:
            parts=name.split('/')
            for part in parts[:-1]:
                nextfd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                opened.append(parent);parent=nextfd
            fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
            try:
                before=os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_uid!=65533:
                    raise ValueError('unsafe_file')
                if before.st_size>spec['single'] or total+before.st_size>spec['total']:
                    raise ValueError('file_limit')
                data=bytearray()
                while True:
                    chunk=os.read(fd,min(65536,spec['single']-len(data)+1))
                    if not chunk: break
                    data.extend(chunk)
                    if len(data)>spec['single'] or total+len(data)>spec['total']:
                        raise ValueError('file_limit')
                after=os.fstat(fd)
                if identity(before)!=identity(after) or len(data)!=before.st_size:
                    raise ValueError('file_changed')
                total+=len(data)
                files.append({'name':name,'data':base64.b64encode(data).decode('ascii'),
                              'sha256':hashlib.sha256(data).hexdigest()})
            finally: os.close(fd)
        finally:
            os.close(parent)
            for fd in opened: os.close(fd)
    print(json.dumps({'files':files},separators=(',',':')))
except (OSError,ValueError):
    print(json.dumps({'error':'export_rejected'}))
finally: os.close(root)
"""

INSTALL = r"""
import base64,hashlib,json,os,re,sys
spec=json.load(sys.stdin);result=[]
root=os.open('/work/inputs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
    for item in spec:
        name=item['name']
        if not name or '/' in name or name in ('.','..'): raise ValueError('invalid_input_name')
        identifier=item['id']
        if not re.fullmatch('art_[0-9a-f]{32}',identifier): raise ValueError('invalid_input_id')
        os.mkdir(identifier,0o700,dir_fd=root)
        parent=os.open(identifier,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
        data=base64.b64decode(item['data'],validate=True)
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444,dir_fd=parent)
        try:
            offset=0
            while offset<len(data): offset+=os.write(fd,data[offset:])
            os.fsync(fd)
            status=os.fstat(fd)
            result.append({'id':identifier,'name':name,'sha256':hashlib.sha256(data).hexdigest(),
                           'uid':status.st_uid,'mode':status.st_mode & 0o777,'size':len(data)})
        finally: os.close(fd)
        os.fchmod(parent,0o555);os.fsync(parent);os.close(parent)
    os.fchmod(root,0o555)
    os.fsync(root)
    status=os.fstat(root)
    print(json.dumps({'files':result,'uid':status.st_uid,'mode':status.st_mode & 0o777}))
finally: os.close(root)
"""
