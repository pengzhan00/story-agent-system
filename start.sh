#!/usr/bin/env bash
set -euo pipefail

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🎬 漫剧故事工坊 — 一键启动
#  用法:
#    ./start.sh              # 正常启动（检查+启动所有服务）
#    ./start.sh --check      # 只检查环境，不启动
#    ./start.sh --restart    # 重启所有服务
#    ./start.sh --comfyui    # 只启动 ComfyUI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[START]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; }

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# 加载 .env
if [ -f ".env" ]; then
    set -a; source .env; set +a
fi

MODE="${1:-normal}"

# ── PID 文件（用于停服/重启） ───────────────────
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

# ── 检查 ComfyUI ─────────────────────────────────
check_comfyui() {
    if curl -s http://127.0.0.1:8188/queue >/dev/null 2>&1; then
        ok "ComfyUI 运行中 (端口 8188)"
        return 0
    else
        return 1
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

# ── 加载 .env（若不存在就创建默认） ──────────────
load_env() {
    if [ ! -f ".env" ]; then
        warn ".env 不存在，创建默认配置"
        cat > .env << 'EOF'
DEFAULT_MODEL=gemma4:latest
CREATIVE_MODEL=gemma4:latest
DETAIL_MODEL=deepseek-r1:70b
COMFYUI_URL=http://127.0.0.1:8188
COMFYUI_DIR=~/Documents/ComfyUI
UI_HOST=127.0.0.1
UI_PORT=7860
EOF
    fi
    set -a; source .env; set +a
}

# ── 启动 ComfyUI ─────────────────────────────────
start_comfyui() {
    if check_comfyui; then
        return 0
    fi
    COMFYUI_DIR="${COMFYUI_DIR:-$HOME/Documents/ComfyUI}"
    info "启动 ComfyUI..."
    if [ -f "$COMFYUI_DIR/.venv/bin/python3" ]; then
        cd "$COMFYUI_DIR"
        nohup ./.venv/bin/python3 main.py --listen 127.0.0.1 > /tmp/comfyui.log 2>&1 &
        echo $! > "$COMFYUI_PIDFILE"
        cd "$PROJECT_DIR"
        # 等几秒
        for i in $(seq 1 10); do
            sleep 1
            if check_comfyui >/dev/null 2>&1; then
                ok "ComfyUI 启动成功"
                return 0
            fi
        done
        warn "ComfyUI 启动可能较慢，请稍后检查: tail -f /tmp/comfyui.log"
    else
        err "ComfyUI venv 未找到 ($COMFYUI_DIR/.venv)"
        err "请先在 ComfyUI 目录创建 venv: cd $COMFYUI_DIR && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
        return 1
    fi
}

# ── 启动 Gradio UI ──────────────────────────────
start_gradio() {
    if check_gradio >/dev/null 2>&1; then
        ok "Gradio UI 已在运行"
        return 0
    fi
    info "启动 Gradio UI (端口 ${UI_PORT:-7860})..."
    nohup python3 main.py > /tmp/storyagent_ui.log 2>&1 &
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
        kill $(cat "$COMFYUI_PIDFILE") 2>/dev/null && ok "ComfyUI 已停止" || true
        rm -f "$COMFYUI_PIDFILE"
    fi
    if [ -f "$GRADIO_PIDFILE" ]; then
        kill $(cat "$GRADIO_PIDFILE") 2>/dev/null && ok "Gradio UI 已停止" || true
        rm -f "$GRADIO_PIDFILE"
    fi
}

# ═══════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  🎬 漫剧故事工坊 — 启动脚本${NC}"
echo -e "${CYAN}  $(date '+%Y-%m-%d %H:%M')${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

load_env

# 激活项目 venv
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

# --check 模式：只检查不启动
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
start_comfyui
start_gradio

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✅ 系统就绪！${NC}"
echo -e "${GREEN}  🌐 UI: http://127.0.0.1:${UI_PORT:-7860}${NC}"
echo -e "${GREEN}  停止: kill \$(cat $GRADIO_PIDFILE)${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
