from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


GENERIC_TITLE_PATTERNS = (
    r"这个\s*AI\s*工具.*值得用吗",
    r"^21\s*岁学生探索",
    r"^今天(?:发现|分享)",
    r"AI\s*神器",
    r"｜候选\d+$",
)


@dataclass(frozen=True)
class CreatorProfile:
    positioning: str = "大学生替普通人筛选真正有用的 AI 与效率工具"
    primary_audience: str = "学生、职场新人和刚开始使用 AI 的普通创作者"
    creator_story: str = "正在用 AI 搭建自己的内容账号和个人工作流"


def _text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values).strip()


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _contains(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def _project_display_name(project: str) -> str:
    return project.rsplit("/", 1)[-1].strip() or "这个工具"


def _category(text: str) -> str:
    if _contains(text, ("trading", "量化", "交易", "股票", "投资", "自动下单")):
        return "finance"
    if _contains(text, ("健身", "动作库", "肌群", "锻炼", "exercise")):
        return "fitness"
    if _contains(text, ("数字女友", "虚拟伴侣", "ai伙伴", "ai 伙伴", "美少女", "live2d", "陪玩")):
        return "ai_companion"
    if _contains(text, ("剪辑", "视频编辑", "短视频", "vlog", "封面", "修图", "图片", "创作者")):
        return "creator_tool"
    if _contains(text, ("windows", "win11", "预装软件", "系统清理", "任务栏")):
        return "system_utility"
    if _contains(text, ("python", "api密钥", "开发者", "程序员", "编程基础", "代码架构", "部署", "自托管", "框架", "repository")):
        return "developer_tool"
    if _contains(text, ("简历", "求职", "岗位", "面试", "作品集")):
        return "career"
    if _contains(text, ("pdf", "论文", "课程", "复习", "学习", "笔记", "资料", "文档聊天")):
        return "learning"
    if _contains(text, ("会议", "录音", "待办", "办公", "职场")):
        return "work_efficiency"
    if _contains(text, ("ai", "人工智能", "智能助手")):
        return "general_ai"
    return "general_utility"


def _source_category(project: Mapping[str, Any]) -> str | None:
    """Classify from source metadata before generated/fallback copy can bias it."""
    text = _text(
        project.get("project_name"),
        project.get("source_description"),
        project.get("source_topics"),
    ).lower()
    if _contains(text, ("trading", "量化", "交易", "股票", "投资", "自动下单")):
        return "finance"
    if _contains(text, ("exercise dataset", "fitness", "workout", "健身动作")):
        return "fitness"
    if _contains(text, ("video editor", "image editor", "photo editor", "design tool", "vlog")):
        return "creator_tool"
    if _contains(text, ("pdf chat", "document chat", "study assistant", "learning assistant")):
        return "learning"
    if _contains(text, ("resume", "job application", "interview assistant")):
        return "career"
    if _contains(text, ("windows utility", "windows tools", "system utility", "debloat")) or (
        "windows" in text and "utilit" in text
    ):
        return "system_utility"
    if _contains(
        text,
        (
            "runtime",
            "compiler",
            "library",
            "framework",
            "sdk",
            "api gateway",
            "developer tool",
            "coding agent",
            "code template",
            "mcp server",
            "asp.net",
        ),
    ):
        return "developer_tool"
    return None


ACCOUNT_FIT = {
    "creator_tool": 96,
    "learning": 92,
    "career": 88,
    "work_efficiency": 84,
    "general_ai": 76,
    "system_utility": 58,
    "ai_companion": 55,
    "general_utility": 48,
    "developer_tool": 34,
    "fitness": 24,
    "finance": 18,
}

CREATOR_CONNECTION = {
    "creator_tool": 98,
    "learning": 88,
    "career": 76,
    "work_efficiency": 72,
    "general_ai": 74,
    "system_utility": 52,
    "ai_companion": 48,
    "general_utility": 45,
    "developer_tool": 42,
    "fitness": 30,
    "finance": 25,
}

BASE_RISK = {
    "finance": 82,
    "ai_companion": 45,
    "system_utility": 36,
    "developer_tool": 28,
    "fitness": 18,
    "general_utility": 16,
    "general_ai": 12,
    "career": 12,
    "work_efficiency": 10,
    "learning": 8,
    "creator_tool": 8,
}


def _audience_match(text: str, category: str) -> float:
    score = 48.0
    if _contains(text, ("学生", "大学生", "职场新人", "普通用户", "创作者", "小红书")):
        score += 32
    if _contains(text, ("无需编程", "不用写代码", "打开即用", "网页", "手机")):
        score += 12
    if _contains(text, ("开发者", "程序员", "需有基础编程", "python", "api密钥", "部署")):
        score -= 32
    if category in {"creator_tool", "learning", "career"}:
        score += 8
    return _clamp(score)


def _demonstrability(text: str, category: str) -> float:
    score = 48.0
    if _contains(text, ("展示", "演示", "对比", "前后", "录屏", "截图", "界面", "gif", "导出")):
        score += 28
    if category in {"creator_tool", "ai_companion", "fitness", "system_utility"}:
        score += 14
    if category == "developer_tool":
        score -= 18
    return _clamp(score)


def _trust_feasibility(text: str, category: str) -> float:
    score = 78.0
    if _contains(text, ("打开即用", "网页", "无需安装", "手机", "拖拽", "本地")):
        score += 12
    if _contains(text, ("python", "api密钥", "部署", "自托管", "命令", "编程基础")):
        score -= 38
    if category == "finance":
        score -= 25
    if category == "system_utility":
        score -= 14
    return _clamp(score)


def _brand_risk(text: str, category: str) -> float:
    risk = float(BASE_RISK[category])
    if _contains(text, ("收益", "自动下单", "赚钱", "投资建议")):
        risk += 14
    if _contains(text, ("数字女友", "陪伴", "美少女")):
        risk += 8
    if _contains(text, ("删除", "系统清理", "关闭追踪", "脚本")):
        risk += 7
    return _clamp(risk)


def title_specificity_score(title: str) -> float:
    text = re.sub(r"\s+", "", str(title or ""))
    if not text:
        return 0.0
    score = 55.0
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in GENERIC_TITLE_PATTERNS):
        score -= 38
    if re.search(r"\d+|PDF|剪映|简历|论文|会议|Windows|OpenCut", text, flags=re.IGNORECASE):
        score += 20
    if any(mark in text for mark in ("？", "?", "之外", "不用", "为了", "能不能", "到底")):
        score += 12
    if 12 <= len(text) <= 32:
        score += 10
    return _clamp(score)


def _condense_problem(text: str, limit: int = 18) -> str:
    cleaned = re.sub(r"[。；;！!]", "，", str(text or "")).split("，", 1)[0]
    cleaned = re.sub(r"^(可以|能够|帮助|让用户|普通人)\s*", "", cleaned).strip()
    return cleaned[:limit].rstrip("，：: ") or "这个真实问题"


def _titles(project: str, category: str, text: str, pain: str) -> list[str]:
    name = _project_display_name(project)
    problem = _condense_problem(pain)
    if category == "creator_tool" and _contains(text, ("剪辑", "视频编辑", "opencut")):
        raw = [
            "为了做AI账号，我找了一个免费的开源剪辑工具",
            "剪映之外，我想试试这个免费的开源剪辑工具",
            f"学生做短视频，{name}能不能省下剪辑成本？",
        ]
    elif category == "learning" and _contains(text, ("pdf", "论文", "资料", "文档")):
        raw = [
            "我把100页PDF交给AI，它能帮我找到重点吗？",
            "不想再翻几十页资料，我准备试试这个AI文档助手",
            f"期末资料太多，{name}能不能先帮我定位重点？",
        ]
    elif category == "career":
        raw = [
            "投出去的简历总没回应，我想让AI先帮我挑问题",
            "同一份简历投不同岗位，重点到底该怎么改？",
            f"求职季前，我准备认真测试一次{name}",
        ]
    elif category == "work_efficiency":
        raw = [
            "上课和开会来不及记，我想让AI先整理一遍",
            f"一段录音交给{name}，它能整理出真正有用的笔记吗？",
            "不想再手抄会议纪要，我准备测试这个本地工具",
        ]
    elif category == "ai_companion":
        raw = [
            "我测试了一个开源AI伙伴，它到底有多像真人？",
            f"能聊天还能陪你打游戏，{name}值得折腾吗？",
            "AI伙伴开始会陪玩了，但普通人真的装得起来吗？",
        ]
    elif category == "system_utility":
        raw = [
            "新电脑预装软件太多，我找到一个开源清理方案",
            f"Windows越用越乱，{name}值得冒险尝试吗？",
            "想把Windows清爽一点，这个工具先要看懂风险",
        ]
    elif category == "finance":
        raw = [
            "AI自动交易看起来很酷，但我不建议普通人直接跟",
            f"{name}热度很高，为什么它不适合这个账号现在发？",
            "会自动下单不等于会赚钱，这类AI工具先别神化",
        ]
    elif category == "fitness":
        raw = [
            "1324个健身动作放在一个库里，收藏前先看适不适合你",
            f"健身小白能不能靠{name}找到标准动作？",
            "这个免费动作库很直观，但它不是我的账号主线",
        ]
    elif category == "developer_tool":
        raw = [
            f"{name}在开发者里很火，但普通人未必需要知道",
            f"这个项目技术价值不低，为什么我暂时不准备发？",
            f"需要写代码才能用，{name}不适合当大众工具推荐",
        ]
    else:
        raw = [
            f"{problem}，{name}能不能真正帮上忙？",
            f"我准备拿一个真实任务测试{name}",
            f"这个工具看起来有用，普通人上手前还要确认三件事",
        ]
    result: list[str] = []
    for title in raw:
        title = re.sub(r"\s+", "", title).strip()
        if title and title not in result:
            result.append(title[:38].rstrip("，：: "))
    return result[:3]


def _cover_text(category: str, titles: list[str]) -> str:
    covers = {
        "creator_tool": "剪映之外\n还有免费选择？",
        "learning": "100页PDF\n让AI帮我找重点？",
        "career": "简历没回应\n先让AI挑问题？",
        "work_efficiency": "来不及记笔记\n让AI先整理？",
        "ai_companion": "AI伙伴\n已经会陪玩了？",
        "system_utility": "Windows太乱\n这个工具敢用吗？",
        "finance": "AI自动交易\n先别急着跟",
        "fitness": "1324个动作\n小白怎么用？",
        "developer_tool": "开发者很火\n普通人需要吗？",
    }
    return covers.get(category, titles[0][:18])


class CreatorStrategyLayer:
    """Judge account fit before content is allowed into the daily publish queue."""

    def __init__(self, profile: CreatorProfile | None = None) -> None:
        self.profile = profile or CreatorProfile()

    def evaluate(
        self, post: Mapping[str, Any], metadata: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        metadata = metadata or {}
        project = str(post.get("project_name") or metadata.get("project") or "").strip()
        if not project:
            raise ValueError("Creator Strategy 候选缺少 project_name")
        corpus = _text(
            project,
            post.get("title"),
            post.get("target_user"),
            post.get("pain_point"),
            post.get("pages"),
            metadata.get("content_angle"),
            metadata.get("target_user"),
            metadata.get("why_people_care"),
        )
        category = str(metadata.get("category_override") or _category(corpus))
        account_fit = float(ACCOUNT_FIT[category])
        audience_match = _audience_match(corpus, category)
        creator_connection = float(CREATOR_CONNECTION[category])
        demonstrability = _demonstrability(corpus, category)
        trust_feasibility = _trust_feasibility(corpus, category)
        risk = _brand_risk(corpus, category)
        title_candidates = _titles(
            project, category, corpus, str(post.get("pain_point") or metadata.get("why_people_care") or "")
        )
        title_score = max(title_specificity_score(title) for title in title_candidates)
        strategy_score = _clamp(
            account_fit * 0.32
            + audience_match * 0.20
            + creator_connection * 0.18
            + demonstrability * 0.15
            + trust_feasibility * 0.15
            - risk * 0.22
        )
        hard_block_reasons: list[str] = []
        if risk >= 70:
            hard_block_reasons.append("品牌或合规风险过高")
        if account_fit <= 35:
            hard_block_reasons.append("偏离当前账号的 AI/效率工具主线")
        if category == "developer_tool" and audience_match < 45:
            hard_block_reasons.append("普通用户需要编程或部署能力")
        decision = (
            "do_not_publish"
            if hard_block_reasons
            else "priority"
            if strategy_score >= 78
            else "optional"
            if strategy_score >= 58
            else "hold"
        )
        why_now = {
            "creator_tool": "你正在搭建 AI 内容账号，剪辑工具与当前成长故事直接相连。",
            "learning": "学生场景与账号受众重合，痛点具体且有长期搜索价值。",
            "career": "求职是学生高频需求，也能自然承接后续工作流产品。",
            "work_efficiency": "普通用户能理解场景，适合用真实录屏验证。",
            "ai_companion": "视觉和讨论性强，但容易把账号带向二次元陪伴赛道。",
            "system_utility": "需求真实，但不是 AI 主线且操作风险需要更重的提示。",
            "fitness": "大众价值不低，但与当前账号承诺关系弱。",
            "finance": "金融风险与受众错配都较高，不适合冷启动账号。",
            "developer_tool": "技术价值可能高，但当前普通用户难以直接获得结果。",
            "general_ai": "符合大方向，但需要更具体的个人实验场景才能发布。",
            "general_utility": "可作为扩展栏目，当前不应挤占 AI 主线。",
        }[category]
        reason = why_now
        if hard_block_reasons:
            reason += " 当前不发：" + "；".join(hard_block_reasons) + "。"
        return {
            "project_name": project,
            "category": category,
            "creator_strategy_score": strategy_score,
            "scores": {
                "account_fit": _clamp(account_fit),
                "audience_match": audience_match,
                "creator_connection": _clamp(creator_connection),
                "demonstrability": demonstrability,
                "trust_feasibility": trust_feasibility,
                "title_specificity": title_score,
                "brand_risk": risk,
            },
            "decision": decision,
            "recommended_or_not": decision in {"priority", "optional"},
            "hard_block_reasons": hard_block_reasons,
            "account_fit_score": _clamp(account_fit),
            "audience_match": audience_match,
            "brand_risk": risk,
            "reason": reason,
            "why_now": why_now,
            "recommended_title": title_candidates[0],
            "title_candidates": title_candidates,
            "cover_text": _cover_text(category, title_candidates),
            "profile": {
                "positioning": self.profile.positioning,
                "primary_audience": self.profile.primary_audience,
                "creator_story": self.profile.creator_story,
            },
            "human_value_score": float(metadata.get("human_value_score") or 0),
        }

    def evaluate_all(
        self,
        posts: Mapping[str, Mapping[str, Any]],
        metadata: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        return [self.evaluate(posts[name], metadata.get(name, {})) for name in sorted(posts)]

    def evaluate_project(self, project: Mapping[str, Any]) -> dict[str, Any]:
        """Evaluate account fit directly from Human Value output.

        The daily pipeline calls this before content generation so rejected
        projects never consume a Content Generator LLM call.
        """
        project_name = str(project.get("project_name") or "").strip()
        post_like = {
            "project_name": project_name,
            "target_user": project.get("target_user"),
            "pain_point": project.get("why_people_care"),
            "pages": [
                project.get("content_angle"),
                project.get("source_description"),
                project.get("source_topics"),
            ],
        }
        metadata = dict(project)
        source_category = _source_category(project)
        if source_category:
            metadata["category_override"] = source_category
        return self.evaluate(post_like, metadata)

    def evaluate_projects(
        self, projects: list[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Evaluate project-level strategy without generated copy."""
        return [self.evaluate_project(project) for project in projects]


def apply_strategy(post: Mapping[str, Any], strategy: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with account-specific titles; never mutate generator output."""
    result = copy.deepcopy(dict(post))
    result["title"] = str(strategy["recommended_title"])
    result["title_candidates"] = list(strategy["title_candidates"])
    result["cover_text"] = str(strategy["cover_text"])
    result["creator_strategy"] = {
        "score": strategy["creator_strategy_score"],
        "decision": strategy["decision"],
        "category": strategy["category"],
    }
    return result


def write_strategy_report(path: Path, strategies: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"projects": strategies}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
