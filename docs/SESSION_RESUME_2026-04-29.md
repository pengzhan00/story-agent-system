# 漫剧故事工坊 — Session 恢复点
## 2026-04-29 16:35

## 项目路径
~/myworkspace/projects/story-agent-system/

## 三管线状态
- A (Flux2Klein4B→Wan2.2): ✅ 294.8s 测试通过
- B (Wan2.2 TI2V 5B GGUF standalone): ✅ 测试通过
- C (AnimateDiff+animagine-xl-3.1): ✅ 含InstantID 9~21s 测试通过

## ComfyUI
- 位置: ~/Documents/ComfyUI/
- 启动: 必须用 ~/Documents/ComfyUI/.venv/bin/python3 (torch 2.11.0)
- start.sh 已全面加强健康检查 (验证torch/节点/补丁)
- 节点补丁: nodes_flux.py CLIPTextEncodeFlux 兼容Klein/Qwen3 token key

## InstantID 集成
- payload key 统一: face_image (兼容旧 reference_face_image)
- 三管线都已注入: C线成功, A/B线 Wan2阶段因SDXL ControlNet不兼容自动降级
- ApplyInstantID 新API需 weight 参数(独立FLOAT, 非 ip_weight/cn_strength)

## 待完成 (下一步: 完整导出单集Ep01)
1. 用 Orchestrator 生成一集完整数据 (5幕×4场×3镜=60个shot)
2. 遍历所有shot: 渲染→TTS→BGM→合成→导出
3. 验证完整Ep01输出

## 启动命令
```bash
# 启动所有服务
cd ~/myworkspace/projects/story-agent-system && ./start.sh

# 环境检查
./start.sh --env
```

## skill
- comfyui-venv-debug (mlops): ComfyUI venv 问题排查
- short-drama-industrialization: 短剧工业化改造
