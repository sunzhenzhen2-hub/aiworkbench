"""项目名称相似度归类（展示期推断 / 历史回填写库复用）。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


DEFAULT_GUESS_THRESHOLD = 0.45

# 集众融合：智安校园等融合类产品
_FUSION_HINTS = re.compile(r"(智安校园|集众融合)", re.I)

# 系统集成：弱电/工程/硬件/运维/城域网等
_INTEGRATION_HINTS = re.compile(
    r"(系统集成|集成项目|城域网|弱电|装修|机房|硬件对接|硬件|"
    r"采购|运维|中毒|病毒|木马|勒索|杀毒|终端故障|宕机|断网|"
    r"网络中断|巡检|专线|交换机|防火墙|补丁|故障处理|告警处置|恢复服务)",
    re.I,
)

# 软件开发
_SOFTWARE_HINTS = re.compile(
    r"(软件开发|软件项目|APP|App|小程序|SaaS|Saas|saas|后端|前端|"
    r"微服务|接口开发|系统开发)",
    re.I,
)

# 更像「杂项日常」而非立项项目的表述
_DAILY_HINTS = re.compile(
    r"(开票|报销|考勤|周报|日报|例会|开会|培训|学习|请假|值班|"
    r"杂项|日常事务|流程申请|经营分析|配合等保|密码测评|"
    r"信息不足|仅记录)",
    re.I,
)

# 更像立项/专项工作
_PROJECT_HINTS = re.compile(
    r"(项目|工程|专项|合同|一期|二期|三期|系统|平台|园区|医院|学校|小学|中学|"
    r"接入|立项|改造|建设|采购|智能交通|一张图|城域网|校园)",
)

_HASH_SUFFIX = re.compile(r"-[0-9a-f]{6,}$", re.I)

# 周报里常见短称呼 → 正式立项名（避免因字太短被误挂日常）
_TITLE_ALIASES = {
    "家医": "家庭医生",
}


def norm_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip().lower())


def canonical_project_name(name: str) -> str:
    """去掉测试用哈希后缀，便于「天网接入专项-xxx」对上正文里的天网。"""
    n = (name or "").strip()
    return _HASH_SUFFIX.sub("", n).strip() or n


def text_similarity(a: str, b: str) -> float:
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 0.85
    return SequenceMatcher(None, na, nb).ratio()


def score_project_match(task_title: str, result_text: str, project_name: str) -> float:
    """对任务卡文字与项目名打分，逻辑与 AI 结构化规则匹配保持一致。"""
    title = (task_title or "").strip()
    result = (result_text or "").strip()
    query = f"{title} {result}".strip()
    names = {project_name, canonical_project_name(project_name)}
    score = 0.0
    for name in names:
        if not name:
            continue
        score = max(score, text_similarity(query, name))
        score = max(score, text_similarity(title, name))
        pname, ttitle = norm_text(name), norm_text(title)
        if title and pname and pname in ttitle:
            score = max(score, 0.8)
        if title and ttitle and ttitle in pname:
            score = max(score, 0.8)
        core = re.sub(r"(项目|专项|工程|合同)$", "", pname)
        if len(core) >= 2 and core in ttitle:
            score = max(score, 0.75)
        if pname and ttitle:
            for i in range(len(pname) - 1):
                chunk = pname[i : i + 2]
                if chunk in ttitle:
                    score = max(score, 0.45)
                    break
    return score


def guess_project_id(
    task_title: str,
    result_text: str,
    projects: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_GUESS_THRESHOLD,
    exclude_names: set[str] | None = None,
) -> tuple[int | None, float, str | None]:
    """
    从项目列表中猜测归属。
    projects 项需含 project_id / name（或 id / name）。
    返回 (project_id|None, confidence, project_name|None)。
    """
    skip = {norm_text(x) for x in (exclude_names or set())}
    best_id: int | None = None
    best_name: str | None = None
    best = 0.0
    for p in projects:
        pid = p.get("project_id", p.get("id"))
        name = p.get("name") or ""
        if pid is None or not name:
            continue
        if norm_text(name) in skip or norm_text(canonical_project_name(name)) in skip:
            continue
        score = score_project_match(task_title, result_text, name)
        if score > best and score >= threshold:
            best = score
            best_id = int(pid)
            best_name = name
    return best_id, round(best, 2) if best_id is not None else 0.0, best_name


# 太常见、不能单独当「上下文关键词」的说法
_CONTEXT_STOP = {
    "项目",
    "任务",
    "处理",
    "跟进",
    "完成",
    "进行",
    "工作",
    "本周",
    "日常",
    "问题",
    "情况",
    "相关",
    "内容",
    "协助",
    "配合",
    "沟通",
    "会议",
    "系统",
    "平台",
    "工程",
    "专项",
}


def _clip(text: str, limit: int = 80) -> str:
    return norm_text(text)[:limit]


def longest_common_piece(a: str, b: str) -> str:
    """两段文字里最长的连续相同片段（用于判断是不是在说同一件事）。"""
    aa, bb = _clip(a), _clip(b)
    if not aa or not bb:
        return ""
    best = ""
    for i in range(len(aa)):
        for j in range(len(bb)):
            k = 0
            while i + k < len(aa) and j + k < len(bb) and aa[i + k] == bb[j + k]:
                k += 1
                if k > 12:
                    break
            if k > len(best):
                best = aa[i : i + k]
    return best


def guess_project_with_context(
    task_title: str,
    result_text: str,
    projects: list[dict[str, Any]],
    context_items: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_GUESS_THRESHOLD,
    exclude_names: set[str] | None = None,
) -> tuple[int | None, float, str | None, str]:
    """
    先按项目名直接匹配；对不上时，看相关人已经归到某项目的任务。
    那些任务里要有和项目名相近的词，并且和当前这句话有共同上下文，才归到该项目。
    context_items 项：project_id、text。
    返回 (project_id|None, confidence, project_name|None, reason)。
    """
    direct_id, direct_conf, direct_name = guess_project_id(
        task_title,
        result_text,
        projects,
        threshold=threshold,
        exclude_names=exclude_names,
    )
    if direct_conf >= 0.75 and direct_id is not None:
        return direct_id, direct_conf, direct_name, "标题与项目名相近"

    skip = {norm_text(x) for x in (exclude_names or set())}
    name_by_id: dict[int, str] = {}
    for p in projects:
        pid = p.get("project_id", p.get("id"))
        name = p.get("name") or ""
        if pid is None or not name:
            continue
        if norm_text(name) in skip or norm_text(canonical_project_name(name)) in skip:
            continue
        name_by_id[int(pid)] = name

    current = f"{task_title or ''} {result_text or ''}".strip()
    by_pid: dict[int, list[str]] = {}
    for item in context_items:
        pid = item.get("project_id")
        text = (item.get("text") or "").strip()
        if pid is None or not text or int(pid) not in name_by_id:
            continue
        bucket = by_pid.setdefault(int(pid), [])
        if len(bucket) < 40:
            bucket.append(text)

    # 某个关键词出现在哪些项目的相关任务里；只出现在一个项目才算有区分度
    piece_projects: dict[str, set[int]] = {}
    for pid, texts in by_pid.items():
        for text in texts:
            seen: set[str] = set()
            for other in texts:
                piece = longest_common_piece(text, other) if other != text else ""
                if len(piece) >= 2 and piece not in _CONTEXT_STOP:
                    seen.add(piece)
            # 也把任务原文里、长度 2～6 且不在停用词里的片段记下来（用和项目名的交集）
            pname = canonical_project_name(name_by_id[pid])
            anchor = longest_common_piece(text, pname)
            if len(anchor) >= 2 and anchor not in _CONTEXT_STOP:
                seen.add(anchor)
            for piece in seen:
                piece_projects.setdefault(piece, set()).add(pid)

    best_id: int | None = None
    best_score = 0.0
    second = 0.0
    for pid, texts in by_pid.items():
        pname = name_by_id[pid]
        related_hit = False
        for text in texts:
            if score_project_match(text, "", pname) >= threshold:
                related_hit = True
                break
            anchor = longest_common_piece(text, canonical_project_name(pname))
            if len(anchor) >= 2 and anchor not in _CONTEXT_STOP:
                related_hit = True
                break
        if not related_hit:
            continue
        overlap = 0.0
        for text in texts:
            piece = longest_common_piece(current, text)
            if len(piece) < 2 or piece in _CONTEXT_STOP:
                continue
            owners = piece_projects.get(piece) or set()
            distinctive = owners <= {pid} or len(piece) >= 4
            in_name = piece in norm_text(canonical_project_name(pname))
            if not (distinctive or in_name):
                continue
            if len(piece) >= 3 or in_name or distinctive:
                overlap = max(overlap, 0.58 + min(len(piece), 8) * 0.04)
                overlap = max(overlap, text_similarity(current, text))
        if overlap > best_score:
            second = best_score
            best_score = overlap
            best_id = pid
        elif overlap > second:
            second = overlap

    if (
        best_id is not None
        and best_score >= 0.55
        and (second == 0 or best_score - second >= 0.08)
    ):
        if direct_id is not None and direct_id != best_id and direct_conf >= threshold:
            return direct_id, direct_conf, direct_name, "标题与项目名相近"
        return (
            best_id,
            round(min(best_score, 0.95), 2),
            name_by_id.get(best_id),
            "与相关人近期同类任务的上下文相近",
        )

    if direct_id is not None:
        return direct_id, direct_conf, direct_name, "标题与项目名相近"
    return None, 0.0, None, ""


def looks_like_daily_work(task_title: str, result_text: str = "") -> bool:
    """明显杂项/行政日常，可归「日常任务」。"""
    title = (task_title or "").strip()
    # 立项痕迹优先：标题像项目则绝不当日常
    if title and _PROJECT_HINTS.search(title):
        return False
    if title in _TITLE_ALIASES:
        return False
    if _DAILY_HINTS.search(title):
        return True
    # 结果里的「原周报导入」等套话不参与判定
    result = (result_text or "").strip()
    result = re.sub(r"原周报导入|本周无实质动作|原文信息不足|仅记录", "", result)
    if _DAILY_HINTS.search(result) and not title:
        return True
    if not title:
        return True
    if len(title) < 3:
        return True
    return False


def looks_like_named_project(task_title: str, result_text: str = "") -> bool:
    """正文明显在写某个立项/专项，不应落「日常任务」。"""
    title = (task_title or "").strip()
    if not title:
        return False
    if title in _TITLE_ALIASES:
        return True
    if looks_like_daily_work(title, result_text):
        return False
    if _PROJECT_HINTS.search(title):
        return True
    # 非杂项、有一定长度的专名，也按立项处理（避免误挂日常）
    if len(title) >= 4:
        return True
    return False


def suggest_project_name_from_title(task_title: str, max_len: int = 64) -> str:
    """从任务概述提炼可建档的项目名。"""
    project_name, _task = split_project_and_task(task_title)
    return (project_name or "")[:max_len]


def split_project_and_task(task_title: str) -> tuple[str, str | None]:
    """
    拆「项目名-任务描述」。
    例：智安校园-实验中学请假系统学生信息录入及家长账号配置
      → («智安校园», «实验中学请假系统学生信息录入及家长账号配置»)
    """
    t = (task_title or "").strip()
    if not t:
        return "", None
    if t in _TITLE_ALIASES:
        return _TITLE_ALIASES[t], None
    t = re.sub(r"\s+", " ", t)
    for sep in (" - ", "——", "–", "-"):
        if sep in t:
            left, right = [x.strip() for x in t.split(sep, 1)]
            if re.fullmatch(r"[0-9a-f]{6,}", right, re.I):
                return left, None
            if len(left) >= 2 and right:
                return left, right
            break
    # 无分隔符：集成/运维类长句尽量收成专名前缀（去掉「处理/排查」等动作尾巴）
    if _INTEGRATION_HINTS.search(t):
        trimmed = re.sub(
            r"(终端)?(中毒|病毒|故障)?(处理|排查|处置|修复|恢复)$",
            "",
            t,
        ).strip(" -/|")
        if len(trimmed) >= 4:
            return trimmed, t
    return t, None


def guess_project_type_from_title(task_title: str, result_text: str = "") -> str:
    """
    根据概述猜测项目类型。
    fusion=集众融合；integration=系统集成；software=软件开发。
    优先级：fusion > integration > software。
    """
    blob = f"{task_title or ''} {result_text or ''}"
    if _FUSION_HINTS.search(blob):
        return "fusion"
    if _INTEGRATION_HINTS.search(blob):
        return "integration"
    if _SOFTWARE_HINTS.search(blob):
        return "software"
    return "software"
