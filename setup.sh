#!/usr/bin/env bash
set -euo pipefail

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🎬 漫剧故事工坊 — Setup 安装脚本
#  在新设备上执行: chmod +x setup.sh && ./setup.sh
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'
step=0

info()  { echo -e "${CYAN}[SETUP]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; }

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  🎬 漫剧故事工坊 — 环境安装${NC}"
echo -e "${CYAN}  $(date '+%Y-%m-%d %H:%M')${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── 1. Python 版本检查 ──────────────────────────
step=$((step+1))
info "[$step/7] Python 版本检查..."
if command -v python3 &>/dev/null; then
    PY=$(python3 --version 2>&1)
    ok "Python: $PY"
else
    err "未安装 Python3，请先安装: brew install python@3.11"
    exit 1
fi

# ── 2. 创建虚拟环境 ──────────────────────────────
step=$((step+1))
info "[$step/7] 创建 Python 虚拟环境..."
if [ -d ".venv" ]; then
    warn ".venv 已存在，跳过"
else
    python3 -m venv .venv
    ok "虚拟环境已创建 (.venv)"
fi
source .venv/bin/activate

# ── 3. 安装 Python 依赖 ─────────────────────────
step=$((step+1))
info "[$step/7] 安装 Python 依赖..."
pip install --upgrade pip -q
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt -q
fi
# 额外核心依赖
pip install gradio requests -q
ok "Python 依赖安装完成"

# ── 4. Ollama 检查 ──────────────────────────────
step=$((step+1))
info "[$step/7] Ollama 检查..."
if command -v ollama &>/dev/null; then
    ok "Ollama 已安装"
    # 检查 ollama 是否运行
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        ok "Ollama 服务运行中"
        # 推荐模型
        for model in "gemma4:latest" "deepseek-r1:70b"; do
            if ollama list 2>/dev/null | grep -q "${model%:*}"; then
                ok " 模型 $model 已存在"
            else
                warn " 模型 $model 未安装，运行: ollama pull $model"
            fi
        done
    else
        warn "Ollama 服务未运行，请执行: ollama serve"
    fi
else
    err "未安装 Ollama，请执行: brew install ollama"
    err "然后: ollama pull gemma4:latest"
fi

# ── 5. ComfyUI 检查 ─────────────────────────────
step=$((step+1))
info "[$step/7] ComfyUI 检查..."
COMFYUI_DIR="${COMFYUI_DIR:-$HOME/Documents/ComfyUI}"
if [ -d "$COMFYUI_DIR" ]; then
    ok "ComfyUI 目录: $COMFYUI_DIR"
    # 检查关键节点
    CUSTOM_NODES="$COMFYUI_DIR/custom_nodes"
    if [ -d "$CUSTOM_NODES/ComfyUI-AnimateDiff-Evolved" ]; then
        ok "  AnimateDiff-Evolved ✓"
    else
        warn "  AnimateDiff-Evolved 未安装，请从 ComfyUI Manager 安装"
    fi
    if [ -d "$CUSTOM_NODES/comfyui_controlnet_aux" ]; then
        ok "  ControlNet Aux ✓"
    else
        warn "  ControlNet Aux 未安装"
    fi
else
    warn "ComfyUI 未找到 (预期: $COMFYUI_DIR)"
    warn "如需渲染功能，请安装 ComfyUI:"
    warn "  git clone https://github.com/comfyanonymous/ComfyUI ~/Documents/ComfyUI"
    warn "  cd ~/Documents/ComfyUI && python3 -m venv .venv && source .venv/bin/activate"
    warn "  pip install -r requirements.txt"
fi

# ── 6. ffmpeg 检查 ──────────────────────────────
step=$((step+1))
info "[$step/7] ffmpeg 检查..."
if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg 已安装 ($(ffmpeg -version 2>&1 | head -1))"
else
    warn "ffmpeg 未安装，请执行: brew install ffmpeg"
fi

# ── 7. 配置检查 ──────────────────────────────
step=$((step+1))
info "[$step/7] 配置文件检查..."
if [ -f ".env" ]; then
    ok ".env 配置文件已存在"
else
    warn ".env 不存在，创建默认配置..."
    cat > .env << 'EOF'
# Ollama 配置
DEFAULT_MODEL=gemma4:latest
CREATIVE_MODEL=gemma4:latest
DETAIL_MODEL=deepseek-r1:70b

# ComfyUI 配置
COMFYUI_URL=http://127.0.0.1:8188
COMFYUI_DIR=~/Documents/ComfyUI

# UI 配置
UI_HOST=127.0.0.1
UI_PORT=7860
EOF
    ok ".env 已创建（请按需修改模型名称）"
fi

# ── 初始化数据库 ─────────────────────────────
info "初始化数据库..."
python3 -c "from core.database import init_db; init_db(); print('数据库初始化完成')" 2>/dev/null && ok "数据库就绪" || warn "数据库初始化跳过（部署后首次启动会自动创建）"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✅ 安装完成！${NC}"
echo -e "${GREEN}  启动命令: ./start.sh${NC}"
echo -e "${GREEN}  或手动:  source .venv/bin/activate && python main.py${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
