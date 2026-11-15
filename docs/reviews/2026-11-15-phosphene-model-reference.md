# 项目7规格失败：服务名误作模型引用

结论：本机历史数据已修复，未重跑生成；不是 Phosphene 服务或权重缺失。

基线：main@7af1238，开工时工作树干净。

## 原因与影响

2026-11-15 的 specs 失败栈为 `Seeder.sync → SpecStore.edit → Settings.model`，错误是
`unknown model ref: 'phosphene'`。项目7有50条当前 `shot.video` 规格携带这个引用，均为
author=agent，历史手工入库备注为“手搓版剧情入库”。`phosphene` 是服务/提供方名称，
不是注册模型ID；项目有效方案 landscape_drama 的 video.generate 模型是 `ltx-2.5-video`。

创建与编辑规格的正常入口已校验模型存在性和类型；历史数据绕过了这一边界。
不能增加提供方同名别名或让未知模型自动回退，这会掩盖错误并在同一提供方有多个模型时误路由。

## 修复

先用 SQLite online backup 创建一致备份，再在事务内通过 `SpecStore.edit` 为这50条规格
追加新版本，仅将 model_ref 改为当前项目配置的 `ltx-2.5-video`。确认没有活跃作业/工作流，
没有锁定规格，author 保留。提示词、负向词、正文、params（含音频/起点）、seed、refs、depends_on
逐项核对不变；旧版本原样保留。没有直接 UPDATE 历史记录，没有创建媒体任务或重试工作流。

- 备份：`data/storyagent.db.bak-before-phosphene-ref-repair-20261115-093858`
- 逐条新旧ID回执：`data/run/phosphene-ref-repair-20261115-093858.json`
- 修复后项目7当前规格中 `model_ref='phosphene'` 数量为0。
- 已失败工作流保留失败状态，用户可重试“规格”阶段；本次不伪造成功状态。

## 防回归与验证

`tests/test_specs.py` 新增历史错误引用恢复测试：正常创建拒绝服务名，模拟旧库错误后普通编辑
复现异常，显式纠正模型时追加版本并保留旧数据、提示词和音频参数，重复修复不产生版本抖动。

验证：规格测试21 passed，Ruff通过，Mypy189源文件通过。没有运行真实模型生成、后端全量或
前端构建；源码运行逻辑未改，不需重启服务。这是定点数据修复，不作全系统发布就绪判定。

规则：外部工具导入规格须经 `SpecStore.create/edit` 或对应API校验，服务名不能写入模型ID字段；
历史错误应明确指定目标模型并保留版本链，不做模糊匹配。
