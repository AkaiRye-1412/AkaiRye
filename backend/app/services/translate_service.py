"""英中翻译服务（首页翻译卡片）：调用已配置的 LLM，返回译文 + 中文讲解。

- 复用 config.llm（OpenAI 兼容协议），与 RAG 共用同一份模型配置；
- 方向支持自动识别：含中日韩统一表意文字即按「中→英」，否则「英→中」；
- 讲解分两套结构（参考有道词典的呈现方式）：
  - 单词/短语（mode=word）：按场景/领域列义项，每项带适用场景与常见搭配例句；
  - 句子/段落（mode=sentence）：关键术语、语法结构、语气场景、易错点四条；
- 模型被要求只输出 JSON；解析失败按模型错误处理，不做"把整段返回当译文"的
  静默降级，避免把讲解文字混进译文。
"""

from __future__ import annotations

import json
import logging
import re

from app.services import config_service

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 5000
DIRECTIONS = ("auto", "en-zh", "zh-en")

SYSTEM_PROMPT = """你是一名资深英汉词典编纂者兼翻译，讲解风格参考有道翻译。按用户给出的方向翻译，并给出讲解。

只输出一个 JSON 对象，不要输出 Markdown 代码块标记或任何额外文字。JSON 结构固定为：
{
  "translation": "译文",
  "mode": "word" 或 "sentence",
  "senses": [
    {
      "pos": "词性缩写，如 n. / v. / adj.，不确定则留空字符串",
      "meaning": "该义项在当前场景下的中文意思",
      "tag": "常用度标注，如 最常用；不需要则留空字符串",
      "scene": "适用领域或场景，一句话",
      "examples": [{"src": "原语搭配或短句", "dst": "对应译文"}]
    }
  ],
  "points": [{"label": "关键术语", "text": "一句话中文讲解"}],
  "tips": "补充提示，一句话，可留空字符串"
}

先判定 mode：
- 原文是一个单词，或不超过 3 个词的短语（如 control、warm-up、control group）→ "word"；
- 其余（整句、多句、段落）→ "sentence"。

word 模式：
1. senses 列 2-5 个义项，按常用度从高到低排序，第一个最常用（tag 写"最常用"）；
2. 义项要覆盖不同专业领域或使用场景（如医学、机械、管理学、日常口语），不要只做同义改写；
3. 每个义项写清 scene（什么领域/场合用这个意思），并给 1-2 个该场景下的常见搭配或短句及译文；
4. 搭配必须是真实常见用法，不确定就少给，绝不编造；
5. points 留空数组；tips 可选，用来提醒词形变化、易混词，或提示"给出完整句子可译得更精确"。

sentence 模式：
1. senses 留空数组；
2. points 固定 3-4 条，label 依次使用"关键术语""语法与结构""语气与场景""易错点"（没有的内容整条省略），text 每条一句话；
3. 关键术语条目要给出「原词 = 中文含义」，必要时补一句搭配说明；
4. tips 可选，一句话补充。

通用要求：
- 译文自然准确，符合目标语言表达习惯，不逐词硬译；
- 保留原文语气、专业术语与格式（段落、列表、代码/变量名不翻译）；
- 原文有拼写或语法错误时，按最可能的本意翻译；
- 全部讲解用中文，总字数控制在 300 字以内，不要编造原文没有的信息。"""


class LlmNotConfigured(RuntimeError):
    """LLM 未配置（API 层转 400 + llm_not_configured，前端引导去设置页）。"""


def detect_direction(text: str) -> str:
    """含中文按中→英，否则英→中（够用即可，不引入语言识别依赖）。"""
    return "zh-en" if re.search(r"[\u4e00-\u9fff]", text) else "en-zh"


def _parse_payload(raw: str) -> dict:
    """容忍 ```json 围栏和前后缀说明，取出第一个完整 JSON 对象。"""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型返回内容不是有效 JSON") from None
        try:
            data = json.loads(s[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("模型返回内容不是有效 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("模型返回内容不是 JSON 对象")
    return data


def _clean_str(value, limit: int = 300, keep_newlines: bool = False) -> str:
    """模型字段可能缺失/非字符串，统一转成去空白的短字符串。"""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return ""
    if keep_newlines:  # 译文要保留段落结构，只压缩行内空白
        cleaned = "\n".join(" ".join(line.split()) for line in value.splitlines())
        return cleaned.strip()[:limit]
    return " ".join(value.split())[:limit]


def _looks_like_word(text: str) -> bool:
    """兜底判词：≤3 个词且没有句末标点，按单词/短语处理。"""
    body = text.strip()
    if not body or len(body) > 40:
        return False
    if re.search(r"[.!?。！？；;\n]", body):
        return False
    return len(body.split()) <= 3


def _normalize_senses(raw) -> list[dict]:
    """义项：只保留有 meaning 的条目，例句做去空与条数裁剪。"""
    if not isinstance(raw, list):
        return []
    senses: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        meaning = _clean_str(item.get("meaning"), 60)
        if not meaning:
            continue
        examples: list[dict] = []
        for eg in item.get("examples") or []:
            if not isinstance(eg, dict):
                continue
            src = _clean_str(eg.get("src"), 160)
            dst = _clean_str(eg.get("dst"), 160)
            if src and dst:
                examples.append({"src": src, "dst": dst})
            elif src:
                examples.append({"src": src, "dst": ""})
            if len(examples) >= 2:
                break
        senses.append(
            {
                "pos": _clean_str(item.get("pos"), 12),
                "meaning": meaning,
                "tag": _clean_str(item.get("tag"), 12),
                "scene": _clean_str(item.get("scene"), 120),
                "examples": examples,
            }
        )
        if len(senses) >= 5:
            break
    return senses


def _normalize_points(raw) -> list[dict]:
    """句译讲解条目：label + text，缺 label 的按「讲解」占位。"""
    if isinstance(raw, list):
        out: list[dict] = []
        for item in raw:
            if isinstance(item, dict):
                text = _clean_str(item.get("text"), 200)
                label = _clean_str(item.get("label"), 20)
            else:
                text, label = _clean_str(item, 200), ""
            if text:
                out.append({"label": label or "讲解", "text": text})
            if len(out) >= 4:
                break
        return out
    if isinstance(raw, dict):
        return [
            {"label": _clean_str(k, 20) or "讲解", "text": _clean_str(v, 200)}
            for k, v in raw.items()
            if _clean_str(v, 200)
        ][:4]
    return []


def _to_markdown(senses: list[dict], points: list[dict], tips: str) -> str:
    """把结构化讲解压成 Markdown，兼容只认 explanation 字段的旧调用方。"""
    lines: list[str] = []
    for s in senses:
        head = s["meaning"]
        if s["pos"]:
            head = f"{s['pos']} {head}"
        if s["tag"]:
            head += f"（{s['tag']}）"
        lines.append(f"**{head}**")
        if s["scene"]:
            lines.append(f"- {s['scene']}")
        if s["examples"]:
            eg = "；".join(f"{e['src']} → {e['dst']}" for e in s["examples"])
            lines.append(f"- 例：{eg}")
    for p in points:
        lines.append(f"- **{p['label']}**：{p['text']}")
    if tips:
        lines.append(f"- {tips}")
    return "\n".join(lines)


def translate(conn, text: str, direction: str = "auto") -> dict:
    """翻译 + 讲解。参数/配置问题抛 ValueError 子类，模型调用失败原样上抛。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("请输入要翻译的内容")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"单次最多翻译 {MAX_INPUT_CHARS} 字，请分段处理")
    if direction not in DIRECTIONS:
        raise ValueError("direction 仅支持 auto / en-zh / zh-en")
    if direction == "auto":
        direction = detect_direction(text)

    cfg = config_service.get_raw(conn, "llm")
    if not cfg.get("base_url") or not cfg.get("model"):
        raise LlmNotConfigured("未配置 LLM 服务：请先在设置页填写接口地址、API Key 与模型名")

    from openai import OpenAI

    client = OpenAI(
        base_url=cfg["base_url"],
        api_key=cfg.get("api_key") or "sk-empty",
        timeout=60,
    )
    direction_label = "中文翻译成英文" if direction == "zh-en" else "英文翻译成中文"
    resp = client.chat.completions.create(
        model=cfg["model"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"翻译方向：{direction_label}\n\n原文：\n{text}"},
        ],
        temperature=0.3,
    )
    raw = (resp.choices[0].message.content or "") if resp.choices else ""
    data = _parse_payload(raw)

    translation = _clean_str(data.get("translation"), 8000, keep_newlines=True)
    if not translation:
        raise ValueError("模型未返回译文，请重试")

    senses = _normalize_senses(data.get("senses"))
    points = _normalize_points(data.get("points"))
    tips = _clean_str(data.get("tips"), 200)

    # mode 以内容为准：有义项就是词条模式，有讲解条目就是句子模式；
    # 模型两者都没给时才退回长度启发式，避免前端拿到空讲解。
    model_mode = _clean_str(data.get("mode"), 16).lower()
    if senses:
        mode = "word"
    elif points:
        mode = "sentence"
    elif model_mode == "word" or _looks_like_word(text):
        mode = "word"
    else:
        mode = "sentence"
    if mode == "word" and not senses and not points:
        points = [{"label": "讲解", "text": "模型未返回该词的义项，请重试或补充上下文。"}]

    explanation = _clean_str(data.get("explanation"), 2000) or _to_markdown(senses, points, tips)

    return {
        "translation": translation,
        "mode": mode,
        "senses": senses,
        "points": points,
        "tips": tips,
        "explanation": explanation,
        "direction": direction,
        "model": cfg["model"],
    }
