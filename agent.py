#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""枫兮助手 - Chatbox 风格的手机端本地大模型助手 (PWA)

启动:  python3 agent.py
访问:  手机浏览器打开 http://电脑内网IP:8000  → 菜单里"添加到主屏幕"即变独立App
依赖:  pip3 install fastapi uvicorn requests python-multipart   （缺哪个装哪个）
功能:  Chatbox风格界面(会话列表/气泡/主题/模型切换) + bash命令执行 + 文件上传
安全:  仅限内网使用, 不要把 8000 端口映射到公网; 每条命令执行前需手动点击确认
"""
import os
import json
import re
import subprocess
import shlex
import threading
import time

import requests
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, Response, JSONResponse
from pydantic import BaseModel

MODEL_DEFAULT = "qwen3.5-35b-a3b"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_TAGS = os.environ.get("OLLAMA_TAGS", "http://127.0.0.1:11434/api/tags")
CHAT_TIMEOUT = 600            # 模型响应超时(秒), CPU推理35B较慢
CWD = "/home/fengxi"          # 命令执行的默认目录 / 上传落盘基准目录
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

# ---------------------------------------------------------------- 后端接口

class ChatIn(BaseModel):
    model: str
    messages: list

class RunIn(BaseModel):
    command: str
    sudo_pass: str = ""

SECRETS_FILE = os.path.join(CWD, ".fx-secrets.json")
MEMORY_FILE = os.path.join(CWD, ".fx-memory.txt")

def load_secrets():
    try:
        with open(SECRETS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

class SecretIn(BaseModel):
    key: str
    value: str

@app.post("/api/secrets")
def api_secrets(b: SecretIn):
    k = re.sub(r"[^A-Z0-9_]", "", (b.key or "").upper())
    if not k:
        return {"ok": False, "error": "bad key"}
    data = load_secrets()
    if b.value:
        data[k] = b.value
        with open(SECRETS_FILE, "w") as f:
            json.dump(data, f)
        try:
            os.chmod(SECRETS_FILE, 0o600)
        except Exception:
            pass
    return {"ok": True, "keys": sorted(data.keys())}

@app.post("/api/chat")
def api_chat(b: ChatIn):
    msgs = [{"role": "system", "content": SYSTEM}] + b.messages
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": b.model, "messages": msgs, "stream": False}, timeout=CHAT_TIMEOUT)
        d = r.json()
        msg = d.get("message") or {}
        return {"reply": msg.get("content", ""), "thinking": msg.get("thinking", "")}
    except Exception as e:
        return {"error": "无法连接本地模型: %s" % e}

@app.post("/api/run")
def api_run(b: RunIn):
    try:
        p = subprocess.run(b.command, shell=True, cwd=CWD,
                           capture_output=True, timeout=120)
        out = (p.stdout + p.stderr).decode("utf-8", "replace")
        if len(out) > 20000:
            out = out[:20000] + "\n...[输出过长已截断]"
        return {"output": out or "(无输出)", "code": p.returncode}
    except subprocess.TimeoutExpired:
        return {"output": "命令超时(120秒), 已终止", "code": -1}
    except Exception as e:
        return {"output": str(e), "code": -1}

TASKS = {}

class TaskIn(BaseModel):
    task: str

@app.post("/api/run_start")
def api_run_start(b: RunIn):
    tid = os.urandom(6).hex()
    cmd = b.command
    if b.sudo_pass and "sudo" in cmd:
        pw = b.sudo_pass.replace("'", "'\\''")
        cmd = "echo '" + pw + "' | sudo -S -p '' bash -c " + shlex.quote(b.command)
    for k, v in load_secrets().items():
        cmd = cmd.replace("{{" + k + "}}", v)
    try:
        p = subprocess.Popen(cmd, shell=True, cwd=CWD,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", start_new_session=True)
    except Exception as e:
        return {"error": str(e)}
    buf = []
    lock = threading.Lock()
    def reader():
        try:
            for line in p.stdout:
                with lock:
                    buf.append(line)
                    if len(buf) > 800:
                        del buf[:300]
        except Exception:
            pass
        try:
            p.wait()
        except Exception:
            pass
    threading.Thread(target=reader, daemon=True).start()
    TASKS[tid] = {"p": p, "buf": buf, "lock": lock, "t0": time.time(), "cmd": b.command}
    now = time.time()
    for k in [k for k, v in list(TASKS.items())
              if v["p"].poll() is not None and now - v["t0"] > 3600]:
        TASKS.pop(k, None)
    return {"task": tid}

@app.get("/api/run_poll")
def api_run_poll(task: str):
    t = TASKS.get(task)
    if not t:
        return {"done": True, "output": "", "code": -1}
    code = t["p"].poll()
    with t["lock"]:
        out = "".join(t["buf"])[-16000:]
    done = code is not None
    if not done and time.time() - t["t0"] > 3600:
        try:
            os.killpg(os.getpgid(t["p"].pid), 15)
        except Exception:
            pass
    return {"done": done, "output": out, "code": code if code is not None else -1}

@app.post("/api/run_stop")
def api_run_stop(b: TaskIn):
    t = TASKS.pop(b.task, None)
    if t:
        try:
            os.killpg(os.getpgid(t["p"].pid), 15)
        except Exception:
            try:
                t["p"].kill()
            except Exception:
                pass
    return {"ok": True}

@app.get("/api/memory")
def api_memory_get():
    try:
        with open(MEMORY_FILE) as f:
            return {"memory": f.read().strip()}
    except Exception:
        return {"memory": ""}

class MemIn(BaseModel):
    text: str

@app.post("/api/memory")
def api_memory_post(b: MemIn):
    text = (b.text or "").strip()
    if not text:
        return {"ok": False}
    lines = []
    try:
        with open(MEMORY_FILE) as f:
            lines = [l.strip() for l in f if l.strip()]
    except Exception:
        lines = []
    lines = [l for l in lines if l != text]
    lines.append(text)
    if len(lines) > 60:
        lines = lines[-60:]
    with open(MEMORY_FILE, "w") as f:
        f.write("\n".join(lines))
    try:
        os.chmod(MEMORY_FILE, 0o600)
    except Exception:
        pass
    return {"ok": True, "count": len(lines)}

class MemClearIn(BaseModel):
    clear: bool = True

@app.post("/api/memory_clear")
def api_memory_clear(b: MemClearIn):
    try:
        os.remove(MEMORY_FILE)
    except Exception:
        pass
    return {"ok": True}

@app.get("/api/tasks")
def api_tasks():
    out = []
    now = time.time()
    for k, v in list(TASKS.items()):
        code = v["p"].poll()
        done = code is not None
        with v["lock"]:
            tail = "".join(v["buf"])[-300:]
        out.append({
            "task": k,
            "cmd": v.get("cmd", ""),
            "done": done,
            "code": code if code is not None else -1,
            "elapsed": int(now - v["t0"]),
            "tail": tail,
        })
        if done and now - v["t0"] > 3600:
            TASKS.pop(k, None)
    return {"tasks": out}

@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    name = os.path.basename(file.filename or "file").replace("..", "").strip()
    if not name:
        name = "file"
    save = os.path.join(UPLOAD_DIR, "%s-%s" % (os.urandom(3).hex(), name))
    with open(save, "wb") as f:
        f.write(await file.read())
    return {"saved": save, "name": name, "size": os.path.getsize(save)}

@app.get("/api/models")
def api_models():
    try:
        r = requests.get(OLLAMA_TAGS, timeout=10)
        return {"models": [m.get("name", "") for m in r.json().get("models", [])]}
    except Exception as e:
        return {"models": [], "error": str(e)}

# ---------------------------------------------------------------- PWA 资源

@app.get("/manifest.json")
def manifest():
    return JSONResponse({
        "name": "知枫语",
        "short_name": "知枫语",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f5f6f8",
        "theme_color": "#e8563f",
        "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml",
                    "purpose": "any"}],
    })

@app.get("/icon.svg")
def icon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
           '<rect width="512" height="512" rx="112" fill="#e8563f"/>'
           '<rect x="92" y="118" width="328" height="236" rx="56" fill="#ffffff"/>'
           '<path d="M150 340 L150 428 L246 340 Z" fill="#ffffff"/>'
           '<circle cx="190" cy="236" r="22" fill="#e8563f"/>'
           '<circle cx="256" cy="236" r="22" fill="#e8563f"/>'
           '<circle cx="322" cy="236" r="22" fill="#e8563f"/></svg>')
    return Response(content=svg, media_type="image/svg+xml")

@app.get("/avatar.svg")
def avatar():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
           '<rect width="512" height="512" rx="256" fill="#8a93a6"/>'
           '<circle cx="256" cy="196" r="86" fill="#ffffff"/>'
           '<path d="M104 452 C104 356 172 316 256 316 C340 316 408 356 408 452 Z" fill="#ffffff"/></svg>')
    return Response(content=svg, media_type="image/svg+xml")

@app.get("/sw.js")
def sw():
    js = ("const CACHE='fengxi-v2';"
          "self.addEventListener('install',e=>{self.skipWaiting();});"
          "self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>"
          "Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));});"
          "self.addEventListener('fetch',e=>{const u=new URL(e.request.url);"
          "if(e.request.method!=='GET'||u.pathname.startsWith('/api/'))return;"
          "e.respondWith(caches.match(e.request).then(c=>c||fetch(e.request).then(r=>{"
          "const cp=r.clone();caches.open(CACHE).then(c2=>c2.put(e.request,cp));return r;})"
          ".catch(()=>caches.match('/'))));});")
    return Response(content=js, media_type="text/javascript")

# ---------------------------------------------------------------- 页面

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML

# ---------------------------------------------------------------- HTML / CSS / JS

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#e8563f">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>枫兮助手</title>
<link rel="manifest" href="/manifest.json">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/icon.svg">
<style>
:root{
  --brand:#e8563f; --brand2:#f0715c;
  --bg:#f5f6f8; --panel:#ffffff; --panel2:#fafafa;
  --text:#1c1c1e; --sub:#8a8a8e; --border:#e6e6ea;
  --bubble-ai:#ffffff; --shadow:0 1px 4px rgba(0,0,0,.06);
}
body.dark{
  --bg:#101014; --panel:#1b1b21; --panel2:#16161b;
  --text:#e9e9ec; --sub:#8b8b93; --border:#2a2a33;
  --bubble-ai:#22222a; --shadow:0 1px 4px rgba(0,0,0,.4);
}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%}
body{font-family:-apple-system,"PingFang SC","Noto Sans SC",sans-serif;background:var(--bg);color:var(--text);overflow:hidden}
#app{display:flex;height:100%;height:100dvh}

/* ---------- 侧边栏 ---------- */
#side{width:264px;background:var(--panel);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0;transition:transform .25s;z-index:30}
#sideHead{padding:18px 16px 12px;display:flex;align-items:center;gap:10px}
#sideHead img{width:34px;height:34px;border-radius:9px}
#sideHead .t{font-weight:700;font-size:17px}
#newChat{margin:4px 12px 10px;padding:11px;border:none;border-radius:12px;background:var(--brand);color:#fff;font-size:15px;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px}
#sessList{flex:1;overflow-y:auto;padding:2px 8px 8px}
.sess{display:flex;align-items:center;padding:11px 10px;border-radius:10px;cursor:pointer;font-size:14px;color:var(--text);gap:6px}
.sess:hover{background:var(--panel2)}
.sess.on{background:var(--panel2);font-weight:600}
.sess span{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sess .del{border:none;background:none;color:var(--sub);font-size:16px;padding:2px 6px;border-radius:6px;display:none;cursor:pointer}
.sess:hover .del,.sess.on .del{display:block}
#sideFoot{padding:10px 12px 14px;border-top:1px solid var(--border);display:flex;gap:8px}
#sideFoot button{flex:1;padding:10px;border:none;border-radius:10px;background:var(--panel2);color:var(--text);font-size:13px;cursor:pointer}
#mask{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:25}

/* ---------- 主区 ---------- */
main{flex:1;display:flex;flex-direction:column;min-width:0}
#top{height:54px;display:flex;align-items:center;gap:10px;padding:0 12px;background:var(--panel);border-bottom:1px solid var(--border);flex-shrink:0}
.iconbtn{border:none;background:none;color:var(--text);font-size:20px;padding:8px 10px;border-radius:10px;cursor:pointer}
#hamb{display:none}
#topTitle{flex:1;font-weight:600;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

#msgs{flex:1;overflow-y:auto;padding:18px 14px 8px;scroll-behavior:smooth}
.welcome{max-width:520px;margin:12vh auto 0;text-align:center;color:var(--sub)}
.welcome img{width:84px;height:84px;border-radius:22px;margin-bottom:16px}
.welcome h2{color:var(--text);font-size:20px;margin-bottom:8px}
.msg{display:flex;margin-bottom:14px;padding:0 4px}
.msg.user{justify-content:flex-end}
.bubble{max-width:82%;padding:10px 14px;border-radius:16px;font-size:15px;line-height:1.65;word-break:break-word;box-shadow:var(--shadow)}
.msg.user .bubble{background:var(--brand);color:#fff;border-bottom-right-radius:5px}
.msg.ai .bubble{background:var(--bubble-ai);border-bottom-left-radius:5px}
.bubble code{background:rgba(125,125,130,.16);padding:1px 5px;border-radius:5px;font-size:13px}
.bubble h1,.bubble h2,.bubble h3{margin:6px 0 4px;font-size:16px}
.bubble li{margin:2px 0 2px 18px}
.bubble a{color:var(--brand)}
.msg.err .bubble{background:#fdecea;color:#b3261e}
body.dark .msg.err .bubble{background:#3a2220;color:#ffb4ab}

/* 命令卡片 */
.cmd{margin:8px 0;border:1px solid var(--border);border-radius:12px;overflow:hidden;background:#0d1117}
body.dark .cmd{background:#0a0a0f}
.cmd-h{display:flex;align-items:center;justify-content:space-between;padding:6px 10px;background:#161b22}
.cmd-lang{color:#8b949e;font-size:12px;font-family:monospace}
.cmd-btns button{border:none;border-radius:7px;padding:4px 10px;font-size:12px;margin-left:6px;cursor:pointer;background:#21262d;color:#c9d1d9}
.cmd-btns .run{background:#238636;color:#fff;font-weight:600}
.cmd pre{padding:10px 12px;overflow-x:auto;color:#e6edf3;font-size:13px;line-height:1.55;font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap}
.cmd-out{margin:8px 0 0;padding:9px 12px;background:#010409;border-radius:9px;color:#7ee787;font-size:12.5px;font-family:ui-monospace,Menlo,monospace;white-space:pre-wrap;max-height:220px;overflow:auto}
/* 头像 */
.msg{align-items:flex-start}
.msg .av{width:32px;height:32px;border-radius:50%;flex-shrink:0;margin-right:8px;background:var(--panel)}
.msg.user .av{margin-right:0;margin-left:8px}

/* 思考过程 */
.think{margin:2px 0 8px;border-left:3px solid var(--brand);padding:2px 0 2px 10px}
.think-h{font-size:12.5px;color:var(--sub);cursor:pointer;user-select:none}
.think-b{display:none;font-size:13px;color:var(--sub);white-space:pre-wrap;margin-top:6px;line-height:1.55;max-height:260px;overflow-y:auto}
.think.open .think-b{display:block}

/* 表情面板 */
#inputbar{position:relative}
#emojiBtn{border:none;background:none;font-size:22px;width:38px;height:42px;flex-shrink:0;cursor:pointer;color:var(--text);display:flex;align-items:center;justify-content:center}
#emojiPanel{display:none;position:absolute;bottom:100%;left:8px;right:8px;background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:8px;box-shadow:0 -6px 24px rgba(0,0,0,.18);max-height:190px;overflow-y:auto;z-index:40}
#emojiPanel.show{display:grid;grid-template-columns:repeat(8,1fr);gap:2px}
#emojiPanel span{font-size:22px;text-align:center;padding:4px 0;cursor:pointer;border-radius:8px}
#emojiPanel span:active{background:var(--panel2)}

.typing{display:inline-flex;gap:5px;padding:14px 16px}
.typing i{width:7px;height:7px;border-radius:50%;background:var(--sub);animation:bl 1.2s infinite}
.typing i:nth-child(2){animation-delay:.2s}.typing i:nth-child(3){animation-delay:.4s}
@keyframes bl{0%,60%,100%{opacity:.25}30%{opacity:1}}

/* ---------- 输入区 ---------- */
#inputbar{display:flex;align-items:flex-end;gap:8px;padding:10px 12px calc(10px + env(safe-area-inset-bottom));background:var(--panel);border-top:1px solid var(--border);flex-shrink:0}
#fileBtn,#sendBtn{border:none;width:42px;height:42px;border-radius:13px;font-size:19px;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center}
#fileBtn{background:var(--panel2);color:var(--text)}
#sendBtn{background:var(--brand);color:#fff;font-size:17px}
#sendBtn:disabled{opacity:.45}
#inp{flex:1;resize:none;border:1px solid var(--border);background:var(--panel2);color:var(--text);border-radius:13px;padding:10px 13px;font-size:15px;line-height:1.5;max-height:120px;outline:none;font-family:inherit}
#inp:focus{border-color:var(--brand)}

/* ---------- 设置弹窗 ---------- */
#modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:50;align-items:flex-end;justify-content:center}
#modal.show{display:flex}
#sheet{width:100%;max-width:480px;background:var(--panel);border-radius:20px 20px 0 0;padding:20px 18px calc(22px + env(safe-area-inset-bottom))}
#sheet h3{font-size:17px;margin-bottom:16px}
.row{margin-bottom:16px}
.row label{display:block;font-size:13px;color:var(--sub);margin-bottom:7px}
.row input,.row select{width:100%;padding:11px 12px;border:1px solid var(--border);border-radius:11px;background:var(--panel2);color:var(--text);font-size:15px;outline:none}
.seg{display:flex;background:var(--panel2);border-radius:11px;padding:4px;gap:4px}
.seg button{flex:1;padding:9px;border:none;border-radius:8px;background:none;color:var(--sub);font-size:14px;cursor:pointer}
.seg button.on{background:var(--brand);color:#fff;font-weight:600}
#installBtn{display:none;width:100%;padding:12px;border:none;border-radius:11px;background:var(--brand);color:#fff;font-size:15px;font-weight:600;cursor:pointer;margin-top:4px}
#closeSet{width:100%;padding:12px;border:none;border-radius:11px;background:var(--panel2);color:var(--text);font-size:15px;cursor:pointer;margin-top:10px}

@media (max-width:899px){
  #hamb{display:block}
  #side{position:fixed;left:0;top:0;bottom:0;transform:translateX(-100%);box-shadow:4px 0 24px rgba(0,0,0,.2)}
  #side.open{transform:translateX(0)}
  #mask.show{display:block}
  .bubble{max-width:88%}
}
</style>
</head>
<body>
<div id="app">
  <aside id="side">
    <div id="sideHead"><img src="/icon.svg" alt=""><div class="t">枫兮助手</div></div>
    <button id="newChat">＋ 新建对话</button>
    <div id="sessList"></div>
    <div id="sideFoot">
      <button id="openSet2">⚙ 设置</button>
    </div>
  </aside>
  <div id="mask"></div>
  <main>
    <div id="top">
      <button class="iconbtn" id="hamb">☰</button>
      <div id="topTitle">枫兮助手</div>
      <button class="iconbtn" id="openSet">⚙</button>
    </div>
    <div id="msgs"></div>
    <div id="inputbar">
      <button id="emojiBtn" title="表情">😊</button>
      <button id="fileBtn" title="上传文件/图片">📎</button>
      <textarea id="inp" rows="1" placeholder="输入消息…"></textarea>
      <button id="sendBtn" title="发送">➤</button>
      <input type="file" id="fileInp" hidden>
      <div id="emojiPanel"></div>
    </div>
  </main>
</div>

<div id="modal">
  <div id="sheet">
    <h3>设置</h3>
    <div class="row">
      <label>外观主题</label>
      <div class="seg">
        <button id="thLight">☀ 浅色</button>
        <button id="thDark">🌙 深色</button>
      </div>
    </div>
    <div class="row">
      <label>模型（自动读取本机 Ollama 已装模型）</label>
      <input id="modelInp" list="modelList" autocomplete="off">
      <datalist id="modelList"></datalist>
    </div>
    <button id="installBtn">📲 安装到主屏幕（变成App）</button>
    <button id="closeSet">完成</button>
  </div>
</div>

<script>
/* ================= 状态 ================= */
let sessions = JSON.parse(localStorage.getItem('fx_sessions')||'[]');
let curId = localStorage.getItem('fx_cur')||null;
let settings = Object.assign({theme:'light', model:'qwen3.5-35b-a3b'},
  JSON.parse(localStorage.getItem('fx_settings')||'{}'));
let sending = false, deferred = null;
const $ = id => document.getElementById(id);

function save(){
  localStorage.setItem('fx_sessions', JSON.stringify(sessions));
  localStorage.setItem('fx_cur', curId||'');
  localStorage.setItem('fx_settings', JSON.stringify(settings));
}
function curSess(){ return sessions.find(s=>s.id===curId); }

/* ================= 渲染 ================= */
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function codeCard(b){
  const enc = esc(b.code);
  const isBash = ['bash','sh','shell'].includes(b.lang);
  const btns = '<button onclick="fxCopy(this)">复制</button>' +
    (isBash ? '<button class="run" onclick="fxRun(this)">▶ 执行</button>' : '');
  return '<div class="cmd"><div class="cmd-h"><span class="cmd-lang">'+(esc(b.lang)||'code')+
    '</span><span class="cmd-btns">'+btns+'</span></div><pre>'+enc+'</pre></div>';
}

function md(src){
  const blocks=[];
  src = String(src).replace(/```(\w*)\n?([\s\S]*?)```/g,(m,lang,code)=>{
    blocks.push({lang:(lang||'').toLowerCase(), code:code.replace(/\n$/,'')});
    return '\u0000B'+(blocks.length-1)+'\u0000';
  });
  let h = esc(src);
  h = h.replace(/^### (.*)$/gm,'<h3>$1</h3>').replace(/^## (.*)$/gm,'<h2>$1</h2>').replace(/^# (.*)$/gm,'<h1>$1</h1>');
  h = h.replace(/\*\*([^*\n]+)\*\*/g,'<b>$1</b>');
  h = h.replace(/`([^`\n]+)`/g,'<code>$1</code>');
  h = h.replace(/^\s*[-*] (.+)$/gm,'<li>$1</li>');
  h = h.replace(/\n/g,'<br>');
  h = h.replace(/\u0000B(\d+)\u0000/g,(m,i)=>codeCard(blocks[+i]||{lang:'',code:''}));
  return h;
}

function renderSide(){
  const el = $('sessList'); el.innerHTML='';
  sessions.slice().reverse().forEach(s=>{
    const d = document.createElement('div');
    d.className = 'sess'+(s.id===curId?' on':'');
    d.innerHTML = '<span>'+esc(s.title)+'</span><button class="del" title="删除">✕</button>';
    d.onclick = e=>{
      if(e.target.classList.contains('del')){
        if(!confirm('删除这个对话?'))return;
        sessions = sessions.filter(x=>x.id!==s.id);
        if(curId===s.id){ curId = sessions.length?sessions[sessions.length-1].id:null; }
        save(); renderSide(); renderMsgs(); return;
      }
      curId = s.id; save(); renderSide(); renderMsgs(); closeSide();
    };
    el.appendChild(d);
  });
}

function bubbleHTML(m){
  let inner = '';
  if(m.role!=='user' && m.thinking){
    inner += '<div class="think" onclick="this.classList.toggle(\'open\')">'+
      '<div class="think-h">💭 思考过程（点击展开/收起）</div>'+
      '<div class="think-b">'+esc(m.thinking)+'</div></div>';
  }
  inner += md(m.content);
  return inner;
}

function renderMsgs(){
  const box = $('msgs'); box.innerHTML='';
  const s = curSess();
  $('topTitle').textContent = s ? s.title : '枫兮助手';
  if(!s || !s.messages.length){
    box.innerHTML = '<div class="welcome"><img src="/icon.svg" alt=""><h2>枫兮助手</h2>'+
      '你好，我是跑在你电脑上的本地助手。<br>可以直接提问，也可以让我操作电脑——<br>'+
      '我会给出 bash 命令，你点「▶ 执行」才会真的运行。<br>支持 📎 上传文件/图片到电脑。</div>';
    return;
  }
  s.messages.forEach(m=>{
    const d = document.createElement('div');
    if(m.role==='user'){
      d.className='msg user';
      d.innerHTML='<div class="bubble">'+bubbleHTML(m)+'</div><img class="av" src="/avatar.svg" alt="">';
    }
    else if(m.role==='err'){
      d.className='msg err';
      d.innerHTML='<img class="av" src="/icon.svg" alt=""><div class="bubble">'+esc(m.content)+'</div>';
    }
    else{
      d.className='msg ai';
      d.innerHTML='<img class="av" src="/icon.svg" alt=""><div class="bubble">'+bubbleHTML(m)+'</div>';
    }
    box.appendChild(d);
  });
  box.scrollTop = box.scrollHeight;
}

/* ================= 会话 ================= */
function newSession(){
  const s = {id: String(Date.now()), title:'新对话', messages:[]};
  sessions.push(s); curId = s.id; save(); renderSide(); renderMsgs(); closeSide();
  $('inp').focus();
  return s;
}

/* ================= 请求 ================= */
async function post(url, data, timeoutMs){
  const ctl = new AbortController();
  const t = setTimeout(()=>ctl.abort(), timeoutMs||300000);
  try{
    const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(data), signal: ctl.signal});
    return await r.json();
  }finally{ clearTimeout(t); }
}

function showTyping(){
  const box=$('msgs');
  const d=document.createElement('div'); d.id='typing'; d.className='msg ai';
  d.innerHTML='<div class="bubble typing"><i></i><i></i><i></i></div>';
  box.appendChild(d); box.scrollTop=box.scrollHeight;
}
function hideTyping(){ const t=$('typing'); if(t)t.remove(); }

async function send(text){
  text = (text||'').trim();
  if(!text || sending) return;
  const s = curSess() || newSession();
  s.messages.push({role:'user', content:text});
  if(s.title==='新对话') s.title = text.slice(0,18) || '新对话';
  save(); renderSide(); renderMsgs();
  await ask(s);
}

async function ask(s){
  sending = true; $('sendBtn').disabled = true; showTyping();
  // 只发最近 30 条, 避免历史过长拖慢 CPU 推理
  const recent = s.messages.slice(-30).map(m=>({role:m.role==='err'?'user':m.role, content:m.content}));
  let r;
  try{ r = await post('/api/chat', {model: settings.model, messages: recent}, 600000); }
  catch(e){ r = {error: '请求失败或超时: '+e}; }
  hideTyping();
  if(r.reply !== undefined){
    s.messages.push({role:'assistant', content:r.reply || '（模型返回空）', thinking:r.thinking||''});
  }else{
    s.messages.push({role:'err', content: r.error || '未知错误'});
  }
  save(); renderMsgs();
  sending = false; $('sendBtn').disabled = false;
}

/* ================= 命令执行 / 复制 ================= */
window.fxCopy = btn=>{
  const code = btn.closest('.cmd').querySelector('pre').textContent;
  navigator.clipboard.writeText(code).then(()=>{ btn.textContent='已复制'; setTimeout(()=>btn.textContent='复制',1200); });
};
window.fxRun = async btn=>{
  if(sending){ alert('请等上一轮回复完成'); return; }
  const card = btn.closest('.cmd');
  const code = card.querySelector('pre').textContent;
  btn.disabled = true; btn.textContent = '⏳ 执行中…';
  const r = await post('/api/run', {command: code}, 150000);
  btn.disabled = false; btn.textContent = '▶ 执行';
  const out = document.createElement('div');
  out.className = 'cmd-out';
  out.textContent = '[退出码 ' + r.code + '] ' + (r.output||'(无输出)');
  card.appendChild(out);
  // 把真实结果回传给模型继续
  const s = curSess();
  if(s){
    s.messages.push({role:'user', content:'[命令执行结果]\n$ '+code+'\n'+(r.output||'(无输出)')});
    save(); renderMsgs(); await ask(s);
  }
};

/* ================= 上传 ================= */
$('fileBtn').onclick = ()=> $('fileInp').click();
$('fileInp').onchange = async ()=>{
  const f = $('fileInp').files[0]; if(!f) return;
  $('fileInp').value='';
  const fd = new FormData(); fd.append('file', f);
  toastUp('上传中: '+f.name);
  try{
    const r = await fetch('/api/upload', {method:'POST', body: fd}).then(x=>x.json());
    await send('[用户上传了文件到电脑: '+r.saved+' ('+Math.round(r.size/1024)+'KB)。请确认收到，等待我的下一步指示。]');
  }catch(e){ alert('上传失败: '+e); }
};
function toastUp(t){
  const d=document.createElement('div');
  d.style.cssText='position:fixed;top:14px;left:50%;transform:translateX(-50%);background:var(--brand);color:#fff;padding:9px 16px;border-radius:20px;font-size:13px;z-index:99;box-shadow:0 4px 14px rgba(0,0,0,.25)';
  d.textContent=t; document.body.appendChild(d); setTimeout(()=>d.remove(),2500);
}

/* ================= 表情面板 ================= */
const EMOJIS = ('😀 😄 😁 😆 😅 🤣 😊 🙂 😉 😍 🥰 😘 😜 🤪 🤔 🤨 😐 😴 😢 😭 😤 😡 🥺 😱 '+
  '🤯 😎 🤩 🥳 🤗 🤫 👍 👎 👌 ✌️ 🤝 💪 👏 🙏 🫶 ❤️ 💔 💯 🔥 ⭐ 🎉 🎊 🍀 🌹 🍉 🍜 '+
  '☕ 🍺 ⚽ 🏀 🎮 💻 📱 🚀 ✅ ❌ ❓ 💡 💰 🐶 🐱 🐼 🦊 🍁 🌙 ☀️ ⛅ 🌈 🎵 🍓 😇').split(' ');
(function(){
  const EP = $('emojiPanel');
  EMOJIS.forEach(e=>{
    const s = document.createElement('span'); s.textContent = e;
    s.onclick = ()=>{ inp.value += e; inp.focus(); };
    EP.appendChild(s);
  });
  $('emojiBtn').onclick = e=>{ e.stopPropagation(); EP.classList.toggle('show'); };
  document.addEventListener('click', e=>{
    if(EP.classList.contains('show') && !EP.contains(e.target)) EP.classList.remove('show');
  });
})();

/* ================= 侧栏 / 输入 ================= */
function openSide(){ $('side').classList.add('open'); $('mask').classList.add('show'); }
function closeSide(){ $('side').classList.remove('open'); $('mask').classList.remove('show'); }
$('hamb').onclick = openSide;
$('mask').onclick = closeSide;
$('newChat').onclick = newSession;

const inp = $('inp');
inp.addEventListener('input', ()=>{ inp.style.height='auto'; inp.style.height=Math.min(inp.scrollHeight,120)+'px'; });
inp.addEventListener('keydown', e=>{
  if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); doSend(); }
});
function doSend(){
  const t = inp.value; inp.value=''; inp.style.height='auto';
  send(t);
}
$('sendBtn').onclick = doSend;

/* ================= 设置 ================= */
function applyTheme(){
  document.body.classList.toggle('dark', settings.theme==='dark');
  $('thLight').classList.toggle('on', settings.theme!=='dark');
  $('thDark').classList.toggle('on', settings.theme==='dark');
  document.querySelector('meta[name=theme-color]').content =
    settings.theme==='dark' ? '#101014' : '#e8563f';
}
$('thLight').onclick = ()=>{ settings.theme='light'; save(); applyTheme(); };
$('thDark').onclick  = ()=>{ settings.theme='dark';  save(); applyTheme(); };

async function loadModels(){
  try{
    const r = await fetch('/api/models').then(x=>x.json());
    const dl = $('modelList'); dl.innerHTML='';
    (r.models||[]).forEach(m=>{ const o=document.createElement('option'); o.value=m; dl.appendChild(o); });
  }catch(e){}
}
function openSet(){
  $('modelInp').value = settings.model;
  loadModels(); applyTheme(); $('modal').classList.add('show');
}
$('openSet').onclick = openSet;
$('openSet2').onclick = ()=>{ closeSide(); openSet(); };
$('closeSet').onclick = ()=>{
  const v = $('modelInp').value.trim();
  if(v){ settings.model = v; save(); }
  $('modal').classList.remove('show');
};
$('modal').onclick = e=>{ if(e.target.id==='modal') $('closeSet').click(); };

/* ================= PWA 安装 ================= */
window.addEventListener('beforeinstallprompt', e=>{
  e.preventDefault(); deferred = e;
  $('installBtn').style.display='block';
});
$('installBtn').onclick = async ()=>{
  if(!deferred) return;
  deferred.prompt();
  await deferred.userChoice;
  deferred = null; $('installBtn').style.display='none';
};
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js').catch(()=>{});
}

/* ================= 启动 ================= */
if(!curId && sessions.length) curId = sessions[sessions.length-1].id;
applyTheme(); renderSide(); renderMsgs();
loadModels();
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
