# -*- coding: utf-8 -*-
"""同步守卫 隔离自测：用伪造快照打逻辑，不碰真实配置。"""
import importlib.util, os, sys, tempfile, hashlib, subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
SRC = os.path.join(_ROOT, 'scripts', 'guard.py')
if not os.path.isfile(SRC):
    SRC = os.path.join(_ROOT, 'guard.py')
spec = importlib.util.spec_from_file_location('sguard', SRC)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

OK = []
BAD = []


def ck(name, cond, extra=''):
    (OK if cond else BAD).append(name)
    print('  %s %s %s' % ('PASS' if cond else 'FAIL', name, extra))


ME = 'LOCALAA-LOCALBB-LOCALCC-LOCALDD-LOCALEE-LOCALFF-LOCALGG-LOCALHH'
PEER = 'PEERAAA-PEERBBB-PEERCCC-PEERDDD-PEEREEE-PEERFFF-PEERGGG-PEERHHH'
DEAD = 'DEADAAA-DEADBBB-DEADCCC-DEADDDD-DEADEEE-DEADFFF-DEADGGG-DEADHHH'


def snap(folders, peer=PEER):
    return {'folders': folders, 'my_id': ME, 'peer': peer, 'dead': {DEAD}}


def fold(fid, devs, ftype='sendreceive'):
    return {'id': fid, 'type': ftype, 'label': fid, 'devices': devs}


def dev(did, pw=None):
    d = {'deviceID': did}
    if pw is not None:
        d['encryptionPassword'] = pw
    return d


print('=' * 66)
print('  同步守卫 · 隔离自测')
print('=' * 66)

# ---- T1 本机自己那条带 9 字符口令 -> 检出并清零
s = snap([fold('wb-skills', [dev(ME, 'test9char'), dev(PEER, '')])])
probs, todo = g.analyse(s)
ck('T1 检出非空口令', any('本机自己' in p and '长度 9' in p for p in probs), str(probs))
ck('T1 产出改写计划', len(todo) == 1)
ck('T1 改写后口令全部为空',
   all(g.pw_len(d)[0] == 0 for d in todo[0]['devices']),
   str([g.pw_len(d)[0] for d in todo[0]['devices']]))

# ---- T2 缺本机自己那条 -> 补上
s = snap([fold('wb-skills', [dev(PEER, '')])])
probs, todo = g.analyse(s)
ck('T2 检出缺本机登记', any('缺「本机自己」' in p for p in probs), str(probs))
ck('T2 补齐后含本机', any(d['deviceID'] == ME for d in todo[0]['devices']))

# ---- T3 残留废弃设备 -> 删除
s = snap([fold('wb-skills', [dev(ME, ''), dev(PEER, ''), dev(DEAD, '')])])
probs, todo = g.analyse(s)
ck('T3 检出废弃设备', any('残留废弃设备' in p for p in probs))
ck('T3 改写后不再含废弃设备',
   all(d['deviceID'] != DEAD for d in todo[0]['devices']))

# ---- T4 干净配置 -> 零问题、零改写
s = snap([fold(f, [dev(ME, ''), dev(PEER, '')]) for f in g.FOLDER_IDS()])
probs, todo = g.analyse(s)
ck('T4 干净时不误报', probs == [] and todo == [], str(probs))

# ---- T5 receiveencrypted 口令合法 -> 不动
s = snap([fold('wb-skills', [dev(ME, 'secret123'), dev(PEER, '')], 'receiveencrypted')])
probs, todo = g.analyse(s)
ck('T5 加密夹不动口令', not any('encryptionPassword' in p for p in probs), str(probs))

# ---- T6 文本类型的口令(pw=None) 不误判
s = snap([fold('wb-skills', [{'deviceID': ME}, {'deviceID': PEER}])])
probs, todo = g.analyse(s)
ck('T6 字段缺失不误判', probs == [] and todo == [], str(probs))

# ---- T7 非四个夹的文件夹不碰
s = snap([fold('other-folder', [dev(ME, 'xxxxxxxxx')])])
probs, todo = g.analyse(s)
ck('T7 无关文件夹不碰', probs == [] and todo == [])

# ---- T8 回传日志写进指定工作区
tmp = tempfile.mkdtemp(prefix='sgtest_')
p = g.write_relay(['结果：**测试**', '主机：`X`'], workspace=tmp)
ck('T8 回传日志已写', bool(p) and os.path.isfile(p), str(p))
if p:
    t = open(p, encoding='utf-8').read()
    ck('T8 日志含结果行', '结果：**测试**' in t)
    ck('T8 日志落在回传日志目录', os.path.basename(os.path.dirname(p)) == '回传日志')
    ck('T8 文件名含主机名', '同步守卫日志-' in os.path.basename(p))

# ---- T9 --check 零写入（比真实 config.xml 的哈希与 mtime）
# 这条需要一台真的装了 Syncthing 的机器。找不到配置文件就跳过，
# 而不是判失败 —— 否则在别人的机器上跑必然误报。
CFG = os.environ.get('SYNCHEAL_CONFIG_XML') or ''
if not CFG:
    _here = os.path.dirname(os.path.abspath(SRC))
    for _c in (os.path.join(_here, 'data', 'config.xml'),
               os.path.join(os.path.expanduser('~'), 'AppData', 'Local',
                            'Syncthing', 'config.xml'),
               os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming',
                            'Syncthing', 'config.xml'),
               r'C:\ProgramData\Syncthing\config.xml'):
        if os.path.isfile(_c):
            CFG = _c
            break
if not (CFG and os.path.isfile(CFG)):
    print('  SKIP T9 未找到 config.xml，跳过「--check 零写入」（在装了 Syncthing 的机器上会实跑）')
else:
    h0 = hashlib.md5(open(CFG, 'rb').read()).hexdigest()
    m0 = os.path.getmtime(CFG)
    PY = sys.executable
    pr = subprocess.run([PY, SRC, '--check'], capture_output=True, text=True,
                        encoding='utf-8', errors='replace', timeout=120)
    h1 = hashlib.md5(open(CFG, 'rb').read()).hexdigest()
    m1 = os.path.getmtime(CFG)
    ck('T9 --check 退出码 0', pr.returncode == 0, str(pr.returncode))
    ck('T9 --check 未改配置', h0 == h1 and m0 == m1)
    ck('T9 --check 输出含结论',
       '体检结论' in (pr.stdout or '') or '只体检模式' in (pr.stdout or ''))

print()
print('=' * 66)
print('  通过 %d / %d' % (len(OK), len(OK) + len(BAD)))
if BAD:
    print('  失败项：')
    for b in BAD:
        print('    · ' + b)
print('=' * 66)
sys.exit(1 if BAD else 0)
