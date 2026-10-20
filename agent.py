#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""枫兮本地助手：手机浏览器指挥电脑干活的最小 Agent

启动:  python3 agent.py
访问:  手机浏览器打开 http://电脑内网IP:8000 （与电脑同WiFi或经Tailscale）
依赖:  pip3 install fastapi uvicorn requests python-multipart   （缺哪个装哪个）
功能:  1) 普通问答  2) 输出 bash 命令供手动执行  3) 手机上传文件/图片到电脑
安全:  仅限内网使用, 不要把 8000 端口映射到公网; 每条命令执行前需手动点击确认
"""
import os
import re
import uuid
import subprocess

import requests
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

MODEL = "qwen3.5-9b"          # Ollama 里的模型名, ollama list 可查
OLLAMA = "http://127.0.0.1:11434/api/chat"
TIMEOUT = 300                  # 模型响应超时(秒)
CWD = "/home/fengxi"           # 命令执行的默认目录 / 上传落盘基准目录
UPLOAD_DIR = os.path.join(CWD, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

SYSTEM = (
    "你是运行在用户本地Ubuntu电脑上的助手。普通问题直接回答。"
    "当任务需要在电脑上实际操作（执行命令、查看文件、安装软件等）时，"
    "输出 ```bash 代码块，每块一条可直接执行的命令，并用一句话说明它的用途。"
    "命令的执行结果会回传给你，请基于真实结果继续，不要编造输出。"
    "用户可能通过手机上传文件到电脑，上传路径会告诉你，你可基于该路径用命令处理文件。"
)

app = FastAPI()
history = [{"role": "system", "content": SYSTEM}]


class Msg(BaseModel):
    text: str


class Cmd(BaseModel):
    command: str


def chat():
    r = requests.post(
        OLLAMA,
        json={"model": MODEL, "messages": history[-24:], "stream": False},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    m = r.json()["message"]
    return (m.get("reasoning") or "", m.get("content") or "")


def split_think(content, reasoning=""):
    m = re.search(r"<think>(.*?)</think>", content, re.S)
    if m:
        return (m.group(1).strip(), content[m.end():].strip())
    return (reasoning.strip(), content.strip())


@app.post("/api/ask")
def ask(m: Msg):
    history.append({"role": "user", "content": m.text})
    try:
        reasoning, content = chat()
    except Exception as e:
        reasoning, content = "", "[调用本地模型失败: %s]" % e
    th, reply = split_think(content, reasoning)
    history.append({"role": "assistant", "content": reply})
    return {"thinking": th, "reply": reply}


@app.post("/api/run")
def run(c: Cmd):
    try:
        p = subprocess.run(
            c.command, shell=True, capture_output=True,
            text=True, timeout=90, cwd=CWD,
        )
        out = p.stdout or ""
        if p.stderr:
            out += "\n[stderr] " + p.stderr
        out = (out.strip() or "(命令执行完毕，无输出)")[:4000]
    except subprocess.TimeoutExpired:
        out = "[命令执行超过90秒，已终止]"
    except Exception as e:
        out = "[执行出错: %s]" % e
    history.append({"role": "user", "content": "[命令执行结果]\n$ %s\n%s" % (c.command, out)})
    th, reply = "", ""
    try:
        reasoning, content = chat()
        th, reply = split_think(content, reasoning)
        history.append({"role": "assistant", "content": reply})
    except Exception as e:
        reply = "[模型总结结果失败: %s]" % e
    return {"output": out, "thinking": th, "reply": reply}


def _safe_name(raw):
    name = os.path.basename(raw or "file")
    # 仅保留安全字符(含中文), 其余替换为下划线, 避免路径穿越/非法字符
    name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", name).strip("._- ")
    return name or "file"


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    name = _safe_name(file.filename)
    dest = os.path.join(UPLOAD_DIR, name)
    if os.path.exists(dest):
        dest = os.path.join(UPLOAD_DIR, "%s_%s" % (uuid.uuid4().hex[:8], name))
    data = await file.read()
    with open(dest, "wb") as f:
        f.write(data)
    return {"saved": dest, "size": len(data), "name": name}


PAGE = r'''<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>枫兮助手</title><style>
body{background:#101418;color:#e8eaed;font-family:system-ui;margin:0;display:flex;flex-direction:column;height:100dvh}
#log{flex:1;overflow-y:auto;padding:12px}
.m{margin:8px 0;max-width:94%;padding:10px 12px;border-radius:12px;white-space:pre-wrap;word-break:break-word;line-height:1.55;font-size:15px}
.u{background:#1f3a5f;margin-left:auto}
.a{background:#1d2126}
details{color:#8a9199;font-size:12px;margin-bottom:6px}
pre{background:#000;padding:8px;border-radius:8px;overflow-x:auto;white-space:pre-wrap;font-size:13px;margin:6px 0}
button.go{margin:2px 8px 6px 0;padding:7px 16px;border:0;border-radius:8px;background:#2e7d32;color:#fff;font-size:14px}
button.skip{padding:7px 14px;border:0;border-radius:8px;background:#444;color:#ccc;font-size:14px}
#bar{display:flex;padding:8px;gap:8px;background:#16191d;border-top:1px solid #2a2f36}
input[type=text]{flex:1;padding:11px;border-radius:8px;border:0;background:#24292f;color:#eee;font-size:15px}
.send{padding:0 16px;border:0;border-radius:8px;background:#1565c0;color:#fff;font-size:15px}
.out{background:#000;color:#9fdf9f;padding:8px;border-radius:8px;white-space:pre-wrap;font-size:13px}
.busy{color:#9aa0a6;font-size:13px}
</style></head><body>
<div id="log"></div>
<div id="bar"><input id="i" type="text" placeholder="输入指令或问题…" autocomplete="off">
<button class="send" onclick="document.getElementById('f').click()">📎</button>
<button class="send" onclick="send()">发送</button></div>
<input type="file" id="f" style="display:none" onchange="up(this)">
<script>
const log=document.getElementById('log');
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}
function add(cls,html){const d=document.createElement('div');d.className='m '+cls;d.innerHTML=html;log.appendChild(d);log.scrollTop=1e9;return d;}
async function post(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json();}
async function send(){
  const i=document.getElementById('i');const t=i.value.trim();if(!t)return;i.value='';
  add('u',esc(t));
  const w=add('a','<span class="busy">思考中…（本地CPU模型较慢，请耐心等待）</span>');
  try{const d=await post('/api/ask',{text:t});render(w,d);}
  catch(e){w.innerHTML='<span class="busy">请求失败: '+esc(e)+'</span>';}
  log.scrollTop=1e9;
}
function fmt(t){
  const parts=String(t||'').split('```');let h='';
  for(let i=0;i<parts.length;i++){
    if(i%2===0){h+=esc(parts[i]);}
    else{
      const nl=parts[i].indexOf('\n');const line=nl<0?parts[i]:parts[i].slice(0,nl).trim();
      const code=nl<0?'':parts[i].slice(nl+1);
      h+='<pre>'+esc(code)+'</pre>';
      if(/^(bash|sh|shell)?\s*$/.test(line)&&code.trim()){
        h+='<button class="go" data-c="'+encodeURIComponent(code)+'">▶ 执行</button><button class="skip" onclick="this.disabled=1;this.textContent=\'已跳过\'">跳过</button>';
      }
    }
  }
  return h;
}
function render(w,d){
  let h='';
  if(d.thinking)h+='<details><summary>思考过程</summary>'+esc(d.thinking)+'</details>';
  h+=fmt(d.reply||'(空回复)');
  w.innerHTML=h;bind(w);
}
function bind(w){w.querySelectorAll('button.go').forEach(b=>b.onclick=()=>run(b));}
async function run(b){
  b.disabled=true;
  const c=decodeURIComponent(b.dataset.c);
  const w=add('a','<span class="busy">正在执行: '+esc(c)+'</span>');
  try{
    const r=await post('/api/run',{command:c});
    let h='<pre class="out">▶ $ '+esc(c)+'\n'+esc(r.output||'')+'</pre>';
    if(r.reply){let h2='';if(r.thinking)h2+='<details><summary>思考过程</summary>'+esc(r.thinking)+'</details>';h2+=fmt(r.reply);h+=h2;}
    w.innerHTML=h;bind(w);
  }catch(e){w.innerHTML='<span class="busy">执行请求失败: '+esc(e)+'</span>';}
  log.scrollTop=1e9;
}
async function up(inp){
  const file=inp.files[0];if(!file)return;
  const fd=new FormData();fd.append('file',file);
  const w=add('a','<span class="busy">正在上传: '+esc(file.name)+' ('+file.size+' 字节) …</span>');
  try{
    const r=await fetch('/api/upload',{method:'POST',body:fd});
    const d=await r.json();
    w.innerHTML='📎 已保存: <pre class="out">'+esc(d.saved)+' ('+d.size+' 字节)</pre>';
    const w2=add('a','<span class="busy">思考中…</span>');
    const d2=await post('/api/ask',{text:'我刚通过手机上传了一个文件到 '+d.saved+'（文件名 '+d.name+'）。如果需要处理它，请用命令基于该路径操作；否则告诉我它已就绪。'});
    render(w2,d2);
  }catch(e){w.innerHTML='<span class="busy">上传失败: '+esc(e)+'</span>';}
  inp.value='';
}
document.getElementById('i').addEventListener('keydown',e=>{if(e.key==='Enter')send();});
add('a','👋 我是你电脑上的本地助手。告诉我你想做什么，或点 📎 把手机上的图片/文件传到电脑（存到 ~/uploads/）。需要操作电脑时我会给出命令，你点「执行」我才动手。');
</script></body></html>'''


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


if __name__ == "__main__":
    import uvicorn
    print("=" * 46)
    print(" 枫兮本地助手已启动")
    print(" 手机浏览器打开: http://<电脑内网IP>:8000")
    print(" (查IP命令: hostname -I)")
    print(" 上传文件落盘目录: %s" % UPLOAD_DIR)
    print(" 按 Ctrl+C 停止服务")
    print("=" * 46)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
