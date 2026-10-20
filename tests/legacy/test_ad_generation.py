#!/usr/bin/env python3
"""快速验证：生成广告项目，检查 dialogue 是否正确生成"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.writer.core import generate_storyline
from core.database import create_project, create_episode, create_shot, init_db

print(f"\n{'='*60}")
print(f"🧪 测试：广告项目生成 + dialogue 验证")
print(f"{'='*60}\n")

# 广告参数
premise = "一款高端智能手表的品牌广告，主角是自信的成功商务人士，展示手表的科技感、精准和品质"
genre = "广告"
tone = "科技、高端、自信"

print(f"📝 创作构想: {premise}")
print(f"🎬 类型/基调: {genre} / {tone}")
print(f"📊 结构: 1幕 × 2场景 × 2镜头 = 4镜头\n")

# 初始化数据库
init_db()

# 创建项目
project_id = create_project({
    "name": "智能手表品牌广告",
    "description": premise[:200],
    "genre": genre,
    "status": "active",
})
print(f"✅ 项目创建: ID={project_id}")

# 生成剧本（使用新的 generate_storyline，包含 dialogue）
print(f"\n✍️ 编剧生成剧本...")
result = generate_storyline(
    premise=premise,
    genre=genre,
    tone=tone,
    acts=1,
    scenes_per_act=2,
    shots_per_scene=2,
    project_id=project_id,
    model="qwen3:8b",  # 使用 Ollama 默认模型
)

if not result or "error" in result:
    print(f"❌ 剧本生成失败: {result}")
    sys.exit(1)

print(f"✅ 剧本生成成功: {result.get('title', '未知')}")
print(f"   synopsis: {result.get('synopsis', '')[:100]}...")

# 检查 dialogue 是否生成
print(f"\n🔍 检查 dialogue 字段...")
acts = result.get("acts", [])
total_shots = 0
shots_with_dialogue = 0
sample_dialogues = []

for act in acts:
    for scene in act.get("scenes", []):
        for shot in scene.get("shots", []):
            total_shots += 1
            dialogue = shot.get("dialogue", [])
            if dialogue and len(dialogue) > 0:
                shots_with_dialogue += 1
                sample_dialogues.append({
                    "shot": shot.get("shot_number", "?"),
                    "location": scene.get("location", "?"),
                    "dialogue": dialogue
                })

print(f"   总镜头数: {total_shots}")
print(f"   有对白镜头: {shots_with_dialogue}")
print(f"   对白覆盖率: {shots_with_dialogue/total_shots*100:.0f}%")

if shots_with_dialogue == 0:
    print(f"\n❌ 问题：所有镜头都没有 dialogue！")
    print(f"   请检查编剧生成是否正确")
    sys.exit(1)

print(f"\n✅ dialogue 生成成功！")

# 显示样本对白
print(f"\n📋 样本对白（前3个镜头）:")
for i, sample in enumerate(sample_dialogues[:3]):
    print(f"\n   镜头 {sample['shot']} @ {sample['location']}")
    for line in sample['dialogue']:
        char = line.get('character', '?')
        text = line.get('line', '?')
        emotion = line.get('emotion', '?')
        print(f"      [{char}] {text} ({emotion})")

# 保存完整结果供查看
output_path = Path(__file__).parent.parent / "output" / "test_ad_result.json"
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\n📁 完整剧本已保存: {output_path}")

print(f"\n{'='*60}")
print(f"✅ 测试通过！编剧现在能生成 dialogue 了")
print(f"{'='*60}\n")