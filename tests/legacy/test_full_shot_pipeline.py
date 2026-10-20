#!/usr/bin/env python3
"""
测试完整镜头流程：渲染 → 合成 → 导出
"""
import json
import sys
import time
import subprocess
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import get_project, list_shots, update_shot, get_shot
from pipelines.render_pipeline import get_dispatcher
from pipelines.compositor import run_compositor_pipeline
from pipelines.audio_pipeline import run_audio_pipeline


def test_single_shot(shot_id: int = 453):
    """测试单个镜头的完整流程"""
    print(f"\n{'='*60}")
    print(f"测试2: 完整镜头流程 (shot_id={shot_id})")
    print(f"{'='*60}\n")
    
    # 1. 获取 shot 数据
    shot = get_shot(shot_id)
    if not shot:
        print(f"❌ Shot {shot_id} 不存在")
        return False
    
    print(f"📍 Shot 信息:")
    print(f"   - 场景: {shot.location}")
    print(f"   - 镜头类型: {shot.shot_type}")
    print(f"   - 状态: {shot.status}")
    print(f"   - 描述: {shot.narration[:50]}...")
    
    # 解析 render_payload
    payload = shot.render_payload
    if isinstance(payload, str):
        payload = json.loads(payload) if payload else {}
    
    if not payload:
        print(f"❌ Shot {shot_id} 没有 render_payload")
        return False
    
    # 2. 渲染视频
    print(f"\n🎬 步骤1: 渲染视频")
    
    # 获取项目信息
    project_id = shot.project_id
    project = get_project(project_id)
    project_name = project.name if project else "test_project"
    
    # 设置输出路径
    base_output = Path.home() / "myworkspace" / "projects" / "story-agent-system" / "output" / project_name
    base_output.mkdir(parents=True, exist_ok=True)
    output_path = base_output / f"shot_{shot_id}_test.mp4"
    
    print(f"   输出路径: {output_path}")
    
    # 使用 RenderDispatcher 渲染
    try:
        dispatcher = get_dispatcher()
        print(f"   可用管线: {[n for n,s in dispatcher.capability_matrix().items() if s.get('available')]}")
        
        start_time = time.time()
        result = dispatcher.render(payload, output_path)
        render_time = time.time() - start_time
        
        if result.path and Path(result.path).exists():
            print(f"   ✅ 渲染成功: {result.path}")
            print(f"   耗时: {render_time:.1f}s")
            
            # 检查视频参数
            probe = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_streams', str(result.path)],
                capture_output=True, text=True
            )
            width = height = duration = "N/A"
            for line in probe.stdout.split('\n'):
                if line.startswith('width='): width = line.split('=')[1]
                if line.startswith('height='): height = line.split('=')[1]
                if line.startswith('duration='): duration = line.split('=')[1]
            print(f"   视频: {width}x{height}, {duration}s")
            
            # 更新 shot 状态
            update_shot(shot_id, {"status": "rendered", "video_path": str(result.path)})
            
            # 3. 测试音频合成（如果需要）
            print(f"\n🔊 步骤2: 音频合成")
            
            # 检查是否有对话/旁白
            dialogue = shot.dialogue
            if isinstance(dialogue, str):
                dialogue = json.loads(dialogue) if dialogue else []
            
            narration = shot.narration
            
            if dialogue or narration:
                print(f"   有对话/旁白，需要 TTS")
                print(f"   对话: {len(dialogue)} 条")
                print(f"   旁白: {narration[:30]}...")
                
                # 运行音频管线
                audio_result = run_audio_pipeline(project_id=project_id)
                
                if audio_result.get("tts"):
                    print(f"   ✅ TTS 生成: {len(audio_result['tts'])} 个音频")
                if audio_result.get("music"):
                    print(f"   ✅ BGM 生成: {len(audio_result['music'])} 个音乐")
            else:
                print(f"   ⚠️ 无对话/旁白，跳过 TTS")
            
            # 4. 合成
            print(f"\n🎞️ 步骤3: 合成视频")
            
            composite_result = run_compositor_pipeline(
                project_id=project_id,
                episode=shot.episode_id,
                burn_subs=True,
                crossfade=0.5
            )
            
            if composite_result.get("success"):
                final_file = composite_result.get("episode_file")
                print(f"   ✅ 合成成功: {final_file}")
                
                # 5. 导出
                print(f"\n📦 步骤4: 导出视频")
                
                export_path = base_output / f"final_shot_{shot_id}.mp4"
                if Path(final_file).exists():
                    import shutil
                    shutil.copy2(final_file, export_path)
                    size_mb = export_path.stat().st_size / 1024 / 1024
                    print(f"   ✅ 导出成功: {export_path}")
                    print(f"   文件大小: {size_mb:.2f} MB")
                    
                    return True
                else:
                    print(f"   ❌ 合成文件不存在")
                    return False
            else:
                print(f"   ❌ 合成失败: {composite_result.get('error')}")
                return False
                
        else:
            print(f"   ❌ 渲染失败")
            return False
            
    except Exception as e:
        print(f"   ❌ 渲染出错: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    shot_id = int(sys.argv[1]) if len(sys.argv) > 1 else 453
    success = test_single_shot(shot_id)
    
    if success:
        print(f"\n✅ 测试完成！")
    else:
        print(f"\n❌ 测试失败")
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())