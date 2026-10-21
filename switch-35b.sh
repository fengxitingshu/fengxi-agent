#!/bin/bash
# 枫兮本地助手：停掉旧的 9B、把 35B-A3B 加载运行起来、手机助手对接新模型
# 前提: qwen3.5-35b-a3b 已经 ollama create 导入好 (若未导入脚本会提示并退出)
# 用法:  bash switch-35b.sh

MODEL_TAG="qwen3.5-35b-a3b"
OLD_TAG="qwen3.5-9b"

# 0. 确认新模型已导入
if ! ollama list 2>/dev/null | grep -q "$MODEL_TAG"; then
  echo "错误: $MODEL_TAG 尚未导入! 请先 ollama create 导入后再运行本脚本。"
  exit 1
fi

# 1. 终止 9B 的 run 进程 + 卸载模型（释放内存，不让它继续占用）
echo "== 终止 $OLD_TAG 进程并卸载 =="
pkill -f "ollama run $OLD_TAG" 2>/dev/null || true
sleep 1
ollama stop "$OLD_TAG" 2>/dev/null || true
echo "已停止 $OLD_TAG (不再占用内存)"

# 2. 把 35B 运行起来（预热加载到内存并常驻；请求级 keep_alive:-1 确保常驻，不必改 ollama.service）
echo "== 加载并运行 $MODEL_TAG (首次加载 20GB 到内存约 1-2 分钟, 请稍候) =="
curl -s -X POST http://127.0.0.1:11434/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL_TAG\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"keep_alive\":-1}" \
  -o /tmp/run35b.json --max-time 600
echo "加载完成 ($MODEL_TAG 已常驻内存, 手机助手可秒回)"

# 3. 手机助手对接新模型
if [ -f "$HOME/agent.py" ]; then
  sed -i 's/^MODEL = "[^"]*"/MODEL = "qwen3.5-35b-a3b"/' "$HOME/agent.py"
  echo "== agent.py 已改为 $MODEL_TAG =="
else
  echo "未找到 ~/agent.py，请手动把 MODEL 改为 $MODEL_TAG"
fi

# 4. 重启助手服务
if [ -f /etc/systemd/system/fengxi-agent.service ]; then
  sudo systemctl restart fengxi-agent 2>/dev/null || true
  echo "== 已重启 fengxi-agent 服务 =="
else
  echo "== 手动模式: 请自行重启 pkill -f agent.py && python3 ~/agent.py =="
fi

echo
echo "完成! 手机刷新 http://电脑IP:8000 即可使用 $MODEL_TAG"
