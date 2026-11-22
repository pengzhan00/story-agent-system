# Flux3 参考图接线示例

用于已有 Flux3 UNET、Qwen3VL 编码器与 Flux2 VAE 的 ComfyUI 安装。模型文件名只是示例；
安装此模板不代表已经安装权重，也不承诺已通过人物一致性的视觉验收。

`references` 把每张参考图变成 LoadImage → 缩放 → VAEEncode → ReferenceLatent，逐张串联后接入
KSampler 的 positive；原来的 FluxGuidance 条件保留。没有输入参考图时仍走原来的文生图链。

本机核对证据（2026-11-22）：
- Pinokio ComfyUI 的 comfy/model_base.py `Flux3.extra_conds` 读取 `reference_latents`。
- comfy/ldm/flux3/model.py 的 preprocess_conds 把引用编码为 image_cond，并区分各张参考。
- 正在运行的 ComfyUI `/object_info/ReferenceLatent` 返回该节点。
- test_adapter_comfy 检查多图链实际进入 sampler positive，未启动真实模型生成。

本机原 `03-1_flux3_klein_-_guidance28-` 模板缺少参考图绑定，2026-11-22 已增加相同 references 声明；
备份为原文件旁的 `.json.bak-20261122-references`。此示例不包含用户的提示词或 LoRA 预设。

运行闭环仍需：选定角色定妆与服装参考 → 将出镜角色绑定到镜头 → 提交明确区分人物、服装和构图用途的参考图。
只有 “he smiles” 或旧镜头单张图片的导入规格，并不等于已经绑定角色定妆。历史成图保留，
需要用户显式重做；不要把接线验证当作成图质量通过。
