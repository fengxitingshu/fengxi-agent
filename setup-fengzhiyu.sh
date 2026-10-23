#!/bin/bash
# setup-fengzhiyu.sh —— 枫知语 一键部署脚本（电脑端）
# 功能: 安装依赖 -> 部署 agent.py -> 配置 systemd 常驻(开机自启/崩溃自愈)
# 用法: bash setup-fengzhiyu.sh
# 说明: 需要已安装 Ollama 并至少导入一个模型(https://ollama.com)
set -e

echo "==> [1/4] 安装 Python 依赖"
PIP_MIRROR="https://mirrors.aliyun.com/pypi/simple/"
pip3 install -q fastapi uvicorn requests python-multipart -i $PIP_MIRROR 2>/dev/null \
  || pip3 install fastapi uvicorn requests python-multipart

echo "==> [2/4] 下载 agent.py (服务端接收器)"
mkdir -p "$HOME"
curl -L -o ~/agent.py "https://ghproxy.net/https://raw.githubusercontent.com/fengxitingshu/fengxi-agent/main/agent.py" \
  || curl -L -o ~/agent.py "https://raw.githubusercontent.com/fengxitingshu/fengxi-agent/main/agent.py"
python3 -m py_compile ~/agent.py && echo "    agent.py 校验通过"

echo "==> [3/4] 安装 systemd 服务 (枫知语接收器)"
sudo tee /etc/systemd/system/fengxi-agent.service > /dev/null <<EOF
[Unit]
Description=fengzhiyu agent (枫知语电脑端接收器)
After=network-online.target ollama.service

[Service]
ExecStart=/usr/bin/python3 $HOME/agent.py
WorkingDirectory=$HOME
User=$USER
Group=$USER
Restart=always
RestartSec=3
Environment=OLLAMA_URL=http://127.0.0.1:11434/api/chat

[Install]
WantedBy=default.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now fengxi-agent

echo "==> [4/4] 完成"
systemctl status fengxi-agent --no-pager | head -6 || true
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo "=========================================="
echo " 部署完成! 手机 App 里填写:"
echo "   大模型地址: http://$IP:11434"
echo "   命令地址:   http://$IP:8000"
echo "   (同 WiFi 直连; 公网用反代时填公网地址并在 App 里配认证)"
echo "=========================================="
