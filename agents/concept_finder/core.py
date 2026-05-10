"""
Concept Finder Agent — 从关键词生成多个差异化故事梗概。
支持纯 LLM 模式和联网搜索增强模式。
"""
import uuid
import json
import requests
from typing import Optional
from core.ollama_client import generate_json, DEFAULT_MODEL, CREATIVE_MODEL


CONCEPT_SYSTEM = """你是一位专业的短剧策划师（IP研发），深耕中国短视频平台（抖音/快手/微信）爆款短剧。
擅长根据关键词生成多个独特、各具差异的故事梗概。

每个梗概要做到：
- 核心爽点突出（隐藏身份/逆袭打脸/甜宠虐恋/重生复仇等）
- 剧名有网感、吸引眼球
- 80字以内梗概，信息密度高
- 不同概念之间：类型、基调、核心冲突要有明显差异

输出纯 JSON，不加任何 markdown 或解释。"""


def _web_search_snippets(keywords: str, n: int = 5) -> str:
    """
    用关键词搜索相关内容，返回摘要文本作为 LLM 的参考上下文。
    失败时静默返回空字符串。
    """
    try:
        # 使用 Bing 非官方搜索接口（无需 API key）
        query = f"{keywords} 短剧 故事 剧情"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(
            "https://www.bing.com/search",
            params={"q": query, "count": n, "setlang": "zh-CN"},
            headers=headers,
            timeout=8,
        )
        if resp.status_code != 200:
            return ""

        # 简单提取 <p> 文本作为摘要
        from html.parser import HTMLParser

        class SnippetParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.snippets: list[str] = []
                self._in_caption = False
                self._buf = ""

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                cls = attrs_dict.get("class", "")
                if tag == "p" or (tag in ("span", "div") and "b_lineclamp" in cls):
                    self._in_caption = True
                    self._buf = ""

            def handle_endtag(self, tag):
                if self._in_caption and tag in ("p", "span", "div"):
                    text = self._buf.strip()
                    if text and len(text) > 20:
                        self.snippets.append(text[:200])
                    self._in_caption = False

            def handle_data(self, data):
                if self._in_caption:
                    self._buf += data

        parser = SnippetParser()
        parser.feed(resp.text)
        snippets = parser.snippets[:n]
        if snippets:
            return "【联网参考】\n" + "\n".join(f"- {s}" for s in snippets)
        return ""
    except Exception:
        return ""


def generate_concepts(
    keywords: str,
    requirements: str = "",
    n: int = 6,
    use_web: bool = False,
    model: str = DEFAULT_MODEL,
    project_id: int = 0,
) -> list[dict]:
    """
    根据关键词生成 n 个差异化故事梗概。

    Args:
        keywords: 关键词，如 "兵王 赘婿 豪门"
        requirements: 用户的额外定制要求
        n: 生成梗概数量 (建议 4-8)
        use_web: 是否先联网搜索相关内容作为参考
        model: LLM 模型
        project_id: 日志关联项目 ID

    Returns:
        list of concept dicts, each with keys:
        id, title, genre, tone, synopsis, hook, tags
    """
    web_context = ""
    if use_web:
        web_context = _web_search_snippets(keywords)

    extra_lines = []
    if requirements:
        extra_lines.append(f"额外定制需求：{requirements}")
    if web_context:
        extra_lines.append(web_context)
    extra_block = ("\n\n" + "\n".join(extra_lines)) if extra_lines else ""

    prompt = f"""根据以下关键词，生成 {n} 个风格完全不同的短剧故事梗概。

关键词：{keywords}{extra_block}

严格要求：
1. {n} 个概念的类型、基调、核心冲突必须各不相同
2. 剧名有网感（可用叠词、数字、感叹等）
3. synopsis ≤80字，突出主角身份+核心冲突+最大爽点
4. hook ≤20字，点明最吸引观众的那一句
5. tags 3-5个标签（题材/爽点/受众）

输出纯 JSON（不加 markdown）：
{{
  "concepts": [
    {{
      "title": "剧名",
      "genre": "类型",
      "tone": "基调",
      "synopsis": "梗概（≤80字）",
      "hook": "核心爽点（≤20字）",
      "tags": ["标签1", "标签2"]
    }}
  ]
}}"""

    result = generate_json(
        prompt=prompt,
        system=CONCEPT_SYSTEM,
        model=model,
        temperature=0.9,
        max_tokens=4096,
        num_ctx=8192,
        project_id=project_id,
        agent_type="concept_finder",
    )

    concepts = result.get("concepts", [])
    # 补全缺失字段 + 分配 ID
    for c in concepts:
        c.setdefault("id", str(uuid.uuid4())[:8])
        c.setdefault("title", "未命名")
        c.setdefault("genre", "")
        c.setdefault("tone", "")
        c.setdefault("synopsis", "")
        c.setdefault("hook", "")
        c.setdefault("tags", [])

    return concepts
