# -*- coding: utf-8 -*-
"""隔离自测：守护脚本「一变就补」是否真的会触发（不碰真实技能目录）"""
import os, sys, time, tempfile, importlib.util, threading, shutil
sys.stdout.reconfigure(encoding='utf-8')

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
SRC = os.path.join(_ROOT, 'scripts', 'sync-watch.py')
if not os.path.isfile(SRC):
    SRC = os.path.join(_ROOT, 'sync-watch.py')
if not os.path.isfile(SRC):
    SRC = os.path.join(os.path.expanduser('~'), '.workbuddy',
                       'sync-heal', 'sync-watch.py')
tmp = tempfile.mkdtemp(prefix='watchtest-')
watch_dir = os.path.join(tmp, 'skills', 'demo-skill')
os.makedirs(watch_dir)
open(os.path.join(watch_dir, 'SKILL.md'), 'w', encoding='utf-8').write('# demo v1\n')

spec = importlib.util.spec_from_file_location('watchmod', SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# 全部重定向到临时目录，绝不碰真实资产
m.WATCH = [os.path.join(tmp, 'skills')]
m.LOG = os.path.join(tmp, 'test.log')
m.LOCK = os.path.join(tmp, '.lock')
m.INTERVAL = 2
m.run_heal = lambda reason: m.log('[STUB] run_heal(%s)' % reason)

t = threading.Thread(target=m.main, daemon=True)
t.start()
time.sleep(1.0)

cases = []
# 用例 1：新增文件 -> 应触发
open(os.path.join(watch_dir, 'SKILL.md'), 'a', encoding='utf-8').write('# demo v2\n')
time.sleep(3.2)
cases.append(('新增/改动文件', '检测到变动' in open(m.LOG, encoding='utf-8').read()))
mid = open(m.LOG, encoding='utf-8').read()

# 用例 2：再等 4 秒什么都不动 -> 不应重复触发
time.sleep(4.0)
after = open(m.LOG, encoding='utf-8').read()
cases.append(('无变动不空转', after.count('检测到变动') == mid.count('检测到变动')))

# 用例 3：新增一个子目录里的文件 -> 应再次触发
os.makedirs(os.path.join(watch_dir, 'references'), exist_ok=True)
open(os.path.join(watch_dir, 'references', 'a.md'), 'w', encoding='utf-8').write('x\n')
time.sleep(3.2)
final = open(m.LOG, encoding='utf-8').read()
cases.append(('深层新增也触发', final.count('检测到变动') >= 2))

print('=' * 60)
print('守护脚本隔离自测')
print('=' * 60)
ok = 0
for name, r in cases:
    print('  [%s] %s' % ('通过' if r else '★失败', name))
    ok += 1 if r else 0
print()
print('  结果：%d/%d 通过' % (ok, len(cases)))
print()
print('  —— 日志原文 ——')
for l in final.splitlines():
    print('   ', l)
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if ok == len(cases) else 1)
