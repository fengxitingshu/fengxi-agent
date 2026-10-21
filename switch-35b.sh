#!/bin/bash
# 枫兮本地助手：切换本地模型到 qwen3.5-35b-a3b（MoE, Q4_K_M, 约20.5GB）
# 用法:  bash switch-35b.sh
# 说明:  会下载新模型、停掉并卸载旧的 qwen3.5-9b、把手机助手(agent.py)切到新模型并重启
#        下载是大文件，中断后重新运行本脚本即可续传/跳过已下载部分

GGUF="Qwen3.5-35B-A3B-Q4_K_M.gguf"
MODEL_DIR="$HOME/qwen35-35ba3b"
GGUF_PATH="$MODEL_DIR/$GGUF"
MODEL_TAG="qwen3.5-35b-a3b"
OLD_TAG="qwen3.5-9b"

export PATH="$HOME/.local/bin:$PATH"

# 1. 下载 + 导入（已导入则整体跳过，省时间）
if ollama list 2>/dev/null | grep -q "$MODEL_TAG"; then
  echo "== 模型 $MODEL_TAG 已存在，跳过下载/导入 =="
else
  if [ ! -f "$GGUF_PATH" ] || [ "$(stat -c%s "$GGUF_PATH" 2>/dev/null || echo 0)" -lt 20000000000 ]; then
    echo "== 下载 $GGUF (约20.5GB, 中断后重跑本脚本可续传) =="
    modelscope download --model unsloth/Qwen3.5-35B-A3B-GGUF \
      --local_dir "$MODEL_DIR" --include "*Q4_K_M.gguf"
  fi
  echo "== 导入 $MODEL_TAG 到 Ollama =="
  echo "FROM $GGUF_PATH" > "$HOME/Modelfile"
  ollama create "$MODEL_TAG" -f "$HOME/Modelfile"
fi

# 2. 停掉并卸载旧的 9B（释放磁盘与内存，32G 跑 35B 需要余量）
echo "== 停止并卸载 $OLD_TAG =="
ollama stop "$OLD_TAG" 2>/dev/null || true
ollama rm "$OLD_TAG" 2>/dev/null || true

# 3. 改手机助手模型 + 重启服务
if [ -f "$HOME/agent.py" ]; then
  sed -i 's/^MODEL = "[^"]*"/MODEL = "qwen3.5-35b-a3b"/' "$HOME/agent.py"
  echo "== agent.py 模型已改为 $MODEL_TAG =="
else
  echo "未找到 ~/agent.py，请手动把 MODEL 改为 $MODEL_TAG"
fi

if [ -f /etc/systemd/system/fengxi-agent.service ]; then
  sudo systemctl restart ollama 2>/dev/null || true
  sudo systemctl restart fengxi-agent 2>/dev/null || true
  echo "== 已重启 ollama + fengxi-agent 服务 =="
else
  echo "== 检测到手动运行模式: 请自行重启:  pkill -f agent.py && python3 ~/agent.py =="
fi

echo
echo "完成! 手机刷新 http://电脑IP:8000 即可使用 $MODEL_TAG"
echo "注意: 35B-A3B 在 32G 内存下走 CPU 推理, 若内存紧张可在对话里 /set parameter num_ctx 4096"
