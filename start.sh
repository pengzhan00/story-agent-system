#!/usr/bin/env bash
set -euo pipefail

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🎬 漫剧故事工坊 — 一键启动
#  用法:
#    ./start.sh              # 正常启动（检查+启动所有服务）
#    ./start.sh --check      # 只检查环境，不启动
#    ./start.sh --restart    # 重启所有服务
#    ./start.sh --comfyui    # 只启动 ComfyUI
#    ./start.sh --env        # 打印环境信息（主要用于诊断）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[START]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; }

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

MODE="${1:-normal}"

# ── PID 文件 ───────────────────────────────────────
COMFYUI_PIDFILE="/tmp/storyagent_comfyui.pid"
GRADIO_PIDFILE="/tmp/storyagent_gradio.pid"

# ── 检查 Ollama ──────────────────────────────────
check_ollama() {
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        ok "Ollama 运行中 (端口 11434)"
        return 0
    else
        err "Ollama 未运行"
        return 1
    fi
}

# ── 检查 ComfyUI（增强版） ────────────────────────
check_comfyui() {
    if ! curl -s http://127.0.0.1:8188/queue >/dev/null 2>&1; then
        return 1
    fi
    COMFYUI_DIR="${COMFYUI_DIR:-$HOME/Documents/ComfyUI}"

    # 1) 检查 Python 是否有 torch（防止用错 venv）
    if [ -f "$COMFYUI_DIR/.venv/bin/python3" ]; then
        local PY="$COMFYUI_DIR/.venv/bin/python3"
        if ! "$PY" -c "import torch; print(torch.__version__)" 2>/dev/null; then
            warn "ComfyUI venv 缺少 torch（$PY），可能渲染失败"
        fi
    fi

    # 2) 检查 CLIPTextEncodeFlux 节点是否正常注册
    local OBJECT_INFO
    OBJECT_INFO=$(curl -s http://127.0.0.1:8188/object_info/CLIPTextEncodeFlux 2>/dev/null)
    local JSON_PY="${COMFYUI_PYTHON:-$COMFYUI_DIR/.venv/bin/python3}"
    if [ ! -f "$JSON_PY" ]; then
        JSON_PY="$(command -v python3)"
    fi
    if echo "$OBJECT_INFO" | "$JSON_PY" -c "import json,sys; d=json.load(sys.stdin); assert d.get('CLIPTextEncodeFlux'), 'missing'" 2>/dev/null; then
        ok "ComfyUI 运行中 (端口 8188) — 节点健全"
        return 0
    else
        warn "ComfyUI 运行中但节点注册可能不完整 (CLIPTextEncodeFlux 缺失)"
        warn "尝试: tail -100 /tmp/comfyui.log | grep -i error"
        return 2
    fi
}

# ── 检查 Gradio ──────────────────────────────────
check_gradio() {
    if curl -s "http://127.0.0.1:${UI_PORT:-7860}/" >/dev/null 2>&1; then
        ok "Gradio UI 运行中 (端口 ${UI_PORT:-7860})"
        return 0
    else
        return 1
    fi
}

# ── 加载 .env（固化环境） ─────────────────────────
load_env() {
    if [ ! -f ".env" ]; then
        warn ".env 不存在，创建默认配置"
        cat > .env << 'EOF'
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  漫剧故事工坊 — 环境配置
#  ⚠️  COMFYUI_PYTHON 必须指向有 torch 的 Python！
#     确认方式: $COMFYUI_PYTHON -c "import torch; print(torch.__version__)"
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Ollama 模型（本地推理用）
DEFAULT_MODEL=gemma4:latest
CREATIVE_MODEL=gemma4:latest
DETAIL_MODEL=deepseek-r1:70b

# ComfyUI
COMFYUI_URL=http://127.0.0.1:8188
COMFYUI_DIR=~/Documents/ComfyUI
# 固定 Python 路径（必须有 torch），启动脚本自动推导
# COMFYUI_PYTHON=~/Documents/ComfyUI/.venv/bin/python3

# UI
UI_HOST=127.0.0.1
UI_PORT=7860
EOF
    fi
    set -a; source .env; set +a
}

# ── 环境诊断 ─────────────────────────────────────
print_env() {
    echo ""
    echo -e "${CYAN}══════════════ 环境诊断 ══════════════${NC}"
    echo "  项目目录:    $PROJECT_DIR"
    echo "  Python (系统): $(which python3 2>/dev/null || echo 'N/A')"
    echo "  Python (项目): $(which python3 2>/dev/null || echo 'N/A')"
    echo "  ComfyUI 目录: ${COMFYUI_DIR:-$HOME/Documents/ComfyUI}"
    local PY="${COMFYUI_PYTHON:-${COMFYUI_DIR:-$HOME/Documents/ComfyUI}/.venv/bin/python3}"
    echo "  ComfyUI Python: ${PY}"
    if [ -f "$PY" ]; then
        local TV=$("$PY" -c "import torch; print(torch.__version__)" 2>/dev/null || echo "❌ 无 torch")
        echo "  ComfyUI torch:  ${TV}"
    else
        echo "  ComfyUI torch:  ❌ python 不存在"
    fi
    echo "  ComfyUI pid:   $(cat "$COMFYUI_PIDFILE" 2>/dev/null || echo 'N/A')"
    echo "  UI 端口:       ${UI_PORT:-7860}"
    echo -e "${CYAN}════════════════════════════════════════${NC}"
    echo ""
}

# ── 启动 ComfyUI（修复版） ────────────────────────
start_comfyui() {
    if check_comfyui >/dev/null 2>&1; then
        return 0
    fi
    COMFYUI_DIR="${COMFYUI_DIR:-$HOME/Documents/ComfyUI}"
    # 优先使用 .env 中固定路径，否则自动推导
    local COMFYUI_PY="${COMFYUI_PYTHON:-$COMFYUI_DIR/.venv/bin/python3}"

    info "启动 ComfyUI..."
    if [ ! -f "$COMFYUI_PY" ]; then
        err "ComfyUI Python 未找到: $COMFYUI_PY"
        err "请检查 .env 中 COMFYUI_DIR 或 COMFYUI_PYTHON 是否正确"
        return 1
    fi

    # 检查 torch
    if ! "$COMFYUI_PY" -c "import torch" 2>/dev/null; then
        err "ComfyUI Python ($COMFYUI_PY) 缺少 torch！"
        err "正确环境: ~/Documents/ComfyUI/.venv/bin/python3"
        return 1
    fi

    # 确保旧进程已清理
    if [ -f "$COMFYUI_PIDFILE" ]; then
        kill "$(cat "$COMFYUI_PIDFILE")" 2>/dev/null || true
        rm -f "$COMFYUI_PIDFILE"
    fi

    cd "$COMFYUI_DIR"
    nohup "$COMFYUI_PY" main.py --listen 127.0.0.1 --port 8188 > /tmp/comfyui.log 2>&1 &
    local PID=$!
    echo $PID > "$COMFYUI_PIDFILE"
    cd "$PROJECT_DIR"

    ok "ComfyUI 启动中 (PID $PID)..."

    # 等待就绪（最长 30 秒）
    for i in $(seq 1 30); do
        sleep 1
        if curl -s http://127.0.0.1:8188/queue >/dev/null 2>&1; then
            # 额外检查——确认节点注册完整
            if curl -s http://127.0.0.1:8188/object_info/CLIPTextEncodeFlux >/dev/null 2>&1; then
                ok "ComfyUI 启动成功（节点健全）"
                return 0
            else
                warn "ComfyUI 响应但节点未就绪（$i 秒），继续等..."
            fi
        fi
    done

    warn "ComfyUI 启动超时，请手动检查:"
    warn "  tail -100 /tmp/comfyui.log | grep -i error"
    return 1
}

# ── 启动 Gradio UI ──────────────────────────────
start_gradio() {
    if check_gradio >/dev/null 2>&1; then
        ok "Gradio UI 已在运行"
        return 0
    fi
    info "启动 Gradio UI (端口 ${UI_PORT:-7860})..."
    nohup .venv/bin/python3 main.py > /tmp/storyagent_ui.log 2>&1 &
    echo $! > "$GRADIO_PIDFILE"
    sleep 3
    if check_gradio >/dev/null 2>&1; then
        ok "Gradio UI 启动成功 → http://127.0.0.1:${UI_PORT:-7860}"
    else
        warn "Gradio UI 可能还在启动，查看: tail -f /tmp/storyagent_ui.log"
    fi
}

stop_all() {
    info "停止所有服务..."
    if [ -f "$COMFYUI_PIDFILE" ]; then
        kill "$(cat "$COMFYUI_PIDFILE")" 2>/dev/null && ok "ComfyUI 已停止" || true
        rm -f "$COMFYUI_PIDFILE"
    fi
    if [ -f "$GRADIO_PIDFILE" ]; then
        kill "$(cat "$GRADIO_PIDFILE")" 2>/dev/null && ok "Gradio UI 已停止" || true
        rm -f "$GRADIO_PIDFILE"
    fi
    # 兜底：杀所有残余 ComfyUI 进程
    lsof -ti :8188 2>/dev/null | xargs kill -9 2>/dev/null || true
}

# ═══════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  🎬 漫剧故事工坊 — 启动脚本${NC}"
echo -e "${CYAN}  $(date '+%Y-%m-%d %H:%M')${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

load_env

# --env 模式：只显示环境信息
if [ "$MODE" = "--env" ]; then
    print_env
    exit 0
fi

# 激活项目 venv（Gradio 运行时需要）
if [ -d ".venv" ]; then
    source .venv/bin/activate
    ok "虚拟环境已激活 (.venv)"
else
    warn "未找到 .venv，请先运行: ./setup.sh"
fi

# 环境检查
echo ""
info "=== 环境检查 ==="
check_ollama || warn "Ollama 需要手动启动: ollama serve"
check_comfyui || warn "ComfyUI 未运行（渲染功能不可用）"
check_gradio || true

# --check 模式
if [ "$MODE" = "--check" ]; then
    echo ""
    info "检查完成（--check 模式，未启动服务）"
    exit 0
fi

# --restart 模式
if [ "$MODE" = "--restart" ]; then
    stop_all
    echo ""
fi

# --comfyui-only 模式
if [ "$MODE" = "--comfyui" ]; then
    start_comfyui
    exit 0
fi

# 正常启动
echo ""
info "=== 启动服务 ==="
start_comfyui || warn "ComfyUI 启动失败，请手动启动"
start_gradio

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✅ 系统就绪！${NC}"
echo -e "${GREEN}  🌐 UI: http://127.0.0.1:${UI_PORT:-7860}${NC}"
echo -e "${GREEN}  停止: ./start.sh --restart${NC}"
print_env
echo -e "${CYAN}  如遇渲染失败，先运行:${NC}"
echo -e "${CYAN}    ./start.sh --check${NC}"
echo -e "${CYAN}    tail -50 /tmp/comfyui.log${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
