# 枫知语 FengZhiYu

跑在**你自己电脑上**的本地 AI 助手 —— 手机 App 直连你电脑的 Ollama 大模型，可对话、可让它执行真实命令、可传文件、内置离线语音识别。数据不出你的内网。

> 原名「枫兮助手 / 知枫语」，现名「**枫知语**」。

## 架构

```
手机 App(枫知语.apk) ──局域网/反代──> 电脑端 agent.py(接收器) ──> Ollama(本地大模型)
```

- **模型生成在电脑后台执行**：手机关掉 App，生成继续跑完，重开自动接续进度；
- **命令真实执行**：模型给出 bash 命令自动在电脑上执行，实时回传输出（支持 sudo 自动提权）；
- **网站凭证**：`{{名称}}` 占位符引用，执行时在电脑端替换，凭证不进聊天记录；
- **内置 Vosk 离线语音**：按住说话，松手转文字并自动发送。

## 电脑端一键部署

```bash
bash setup-fengzhiyu.sh
```

脚本自动完成：安装 Python 依赖 → 部署 agent.py → 配置 systemd 常驻（开机自启、崩溃自愈）。前提：电脑已装 [Ollama](https://ollama.com) 并导入模型（如 `qwen3.5` 系列）。

手动部署等价命令：

```bash
pip3 install fastapi uvicorn requests python-multipart
curl -L -o ~/agent.py https://ghproxy.net/https://raw.githubusercontent.com/fengxitingshu/fengxi-agent/main/agent.py
sudo cp fengxi-agent.service /etc/systemd/system/   # 按需修改 User/路径
sudo systemctl daemon-reload && sudo systemctl enable --now fengxi-agent
```

## 手机 App 下载

从 [Releases](../../releases) 下载最新 `assistant.apk` 安装（需允许未知来源）。

- 首次打开 → 设置里填电脑的「大模型地址」(如 `http://192.168.1.100:11434`) 和「命令地址」(如 `http://192.168.1.100:8000`) → 测试连接；
- 公网使用建议走反向代理 + BasicAuth + HTTPS（App 设置里支持填认证账号密码、可跳过自签证书校验）；
- 长期记忆、任务记录、模型切换、深浅主题等在 App 内设置。

## 文件说明

| 文件 | 用途 |
|---|---|
| `agent.py` | 电脑端接收器（命令执行/文件接收/后台生成/凭证管理），单文件 |
| `setup-fengzhiyu.sh` | 电脑端一键部署脚本 |
| `fengxi-agent.service` | systemd 常驻服务文件 |
| `switch-35b.sh` | 切换 Ollama 模型辅助脚本 |

## 安全提示

- 8000 端口可在电脑上执行任意命令，**切勿映射公网裸奔**；公网使用务必套反代认证 + HTTPS，或走 Tailscale 等虚拟组网；
- App 与电脑间为局域网通信，凭证只存电脑端本地文件（0600）。
