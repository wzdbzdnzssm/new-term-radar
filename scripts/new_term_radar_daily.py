#!/usr/bin/env python3
"""Run the free radar sources only; paid sources remain explicitly disabled."""
from pathlib import Path
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT=Path('/home/admin/new-term-radar')
PYTHON=Path('/home/admin/.hermes/venvs/new-term-radar/bin/python')
STATE=Path('/home/admin/.hermes/data/new-term-radar')
OUTPUT=Path('/home/admin/.hermes/outputs/new-term-radar')
SKIP='x,sitemaps,newsites,gsc,review,judge,usecases,revenue,asks,tutorials'
MONITOR=Path('/home/admin/.hermes/data/ai-video-monitor')
sys.path.insert(0,str(ROOT))
from radar.monitor_bridge import atomic_write, encoded, run_bridge


def bridge_report():
    try:
        data=run_bridge(MONITOR,STATE/'monitor-bridge',OUTPUT)
        return {key:data[key] for key in ('status','source_count','accepted_count','addition_count','errors')} | {
            'json':str(OUTPUT/'sitemap-monitor.json'),'markdown':str(OUTPUT/'sitemap-monitor.md'),
            'channel':'unevaluated_sitemap_candidates'}
    except Exception as exc:
        return {'status':'degraded','errors':[type(exc).__name__+': bridge unavailable; no no-new-terms claim'],
                'channel':'unevaluated_sitemap_candidates'}


def publish(out, report, data, bridge):
    data['monitor_bridge']=bridge
    report+='\n\n## 本地 sitemap monitor（独立未评估候选通道）\n\n'
    report+=f"状态：{bridge['status']}；成功来源：{bridge.get('accepted_count',0)}/{bridge.get('source_count',0)}；桥接 URL 新增：{bridge.get('addition_count',0)}。\n"
    report+='URL 文本仅为候选；first_seen/lastmod 不是发布日期；多个域名不代表独立企业；不写入原生 leads/MCP。零新增不等于没有新词。\n'
    if 'markdown' in bridge:
        report+=f"报告：{bridge['markdown']}\nJSON：{bridge['json']}\n"
    if bridge.get('errors'):
        report+='桥接已降级：'+'；'.join(bridge['errors'])+'\n'
    for path in (out,OUTPUT/'latest.md'):
        atomic_write(path,report)
    for path in (out.with_suffix('.json'),OUTPUT/'latest.json'):
        atomic_write(path,encoded(data))


def main():
    STATE.mkdir(parents=True,exist_ok=True)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    with (STATE/'run.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        env=dict(os.environ)
        # Defense in depth: no paid request is authorized by this wrapper.
        for key in ['AISA_API_KEY','GOANYAPI_API_KEY','TRUSTMRR_API_KEY','RESEND_API_KEY']:
            env.pop(key,None)
        bridge=bridge_report()
        try:
            auth=subprocess.run(['gh','auth','token'],capture_output=True,text=True,timeout=10)
            if auth.returncode==0 and auth.stdout.strip():env['GITHUB_TOKEN']=auth.stdout.strip()
        except (OSError,subprocess.TimeoutExpired):
            pass
        stamp=dt.datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
        out=OUTPUT/('radar-'+stamp+'.md')
        command=[str(PYTHON),'-m','radar','daily','--config',str(ROOT/'config.json'),'--state',str(STATE),'--out',str(out),'--skip',SKIP]
        try:
            r=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=300)
            if r.returncode:
                raise RuntimeError('radar subprocess failed')
            report=out.read_text(encoding='utf-8')
            data=json.loads(out.with_suffix('.json').read_text())
        except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
            publish(out,'新词雷达免费来源运行失败或超时；这是降级，不代表没有新词。\n',
                    {'status':'degraded','error':type(exc).__name__},bridge)
            print('新词雷达已降级；本地桥接状态：'+bridge['status']+'\nMEDIA:'+str(out),file=sys.stderr)
            raise SystemExit(1)
        publish(out,report,data,bridge)
        if bridge['status']!='ok' or 'HTTPError' in report or 'TimeoutError' in report or 'URLError' in report:
            print('新词雷达已生成部分报告，但网络来源或本地桥接覆盖已降级；不等于没有新词。\nMEDIA:'+str(out))
        else:
            print('新词雷达免费版已运行：Hugging Face＋GitHub。X、关键词量化和付费模型判断未开启。\nMEDIA:'+str(out))


if __name__=='__main__':
    try:main()
    except subprocess.TimeoutExpired:
        print('新词雷达运行超时，未宣称成功。',file=sys.stderr);sys.exit(1)
