"""Deterministic editorial sufficiency and compositional fact binding."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
import unicodedata

from src.briefing.models import SourceEvidence
from src.briefing.opinion import x_content_rejection_reason


EVENT_ACTION_MARKERS = {
    "release": (
        "发布", "推出", "介绍", "上线", "可用", "公开", "开放使用", "release", "released", "releases",
        "launch", "launched", "launches", "available", "receiving access", "introduce",
        "introduced", "introduces", "introducing", "releasing", "is live",
        "goes live", "went live", "roll out", "rolling out", "rollout",
        "launching", "releasing",
        # Relaunch/reissue and Chinese "major rewrite / opened free" product news are
        # asserted releases; they were previously dropped as no_asserted_action.
        "relaunch", "relaunches", "relaunched", "relaunching",
        "大重构", "免费开放", "开放下载",
        # Establishing an institute/initiative is rendered as 成立/设立 and maps to
        # the same launch action the English source uses ("launches institute").
        "成立", "设立", "创建",
        # Chinese renderings of relaunch/launch actions that the content LLM emits.
        "重新推出", "重启", "启动", "上线了",
    ),
    "update": (
        "更新", "升级", "新增", "下线", "update", "updated", "updates",
        "updating", "upgrade", "upgraded", "upgrades", "upgrading",
        "add", "added", "adds", "adding",
        "deprecate", "deprecated", "deprecating", "暂停", "追踪", "跟踪",
        "pause", "paused", "pauses", "pausing", "tracks", "tracking",
    ),
    "result": (
        "达到", "提升", "降低", "减少", "超过", "增长", "achieve", "achieved", "improve",
        "improved", "improving", "reduce", "reduced", "reduces", "reducing",
        # Price-cutting verbs. The Chinese 降低 was listed but the English
        # "slashes prices" was not, so a correct translation asserting 降低 was
        # rejected as action_not_supported while the source said "slashes ...
        # prices by up to 95 percent".
        "slash", "slashes", "slashed", "slashing",
        "exceed", "exceeded", "exceeding", "jump", "jumps",
        # Measured/superlative outcomes that assert an event ("beats X", "now leads").
        "beats", "beat", "leads", "lead",
        # Chinese renderings of measured outcomes.
        "承担", "领先", "反超", "领导",
        # Chinese renderings of "beats"/an outright decided win, aligning with the
        # English beats/beat group. Planned or conditional framing ("或将击败",
        # "可能击败") is already blocked by _PLANNED_ACTION before this table.
        "击败", "战胜", "胜过", "赢得", "夺得", "登顶", "斩获",
    ),
    "research": (
        "研究发现", "论文提出", "实验显示", "发表论文", "发表",
        "study finds", "paper proposes", "publishes a paper", "published a paper",
        "publishes", "published", "publishing",
    ),
    "funding": (
        "完成融资", "获得融资", "获得投资", "融资完成", "raise", "raises",
        "raised", "funded", "raising",
    ),
    "acquisition": (
        "收购", "完成合并", "acquire", "acquired", "acquires", "acquiring",
        "merges with", "merging with",
    ),
    "partnership": (
        "达成合作", "宣布合作", "签署合作", "partners with", "partnered with",
        "partnering with", "collaborates with", "collaborated with",
        "collaborating with",
        # Chinese partnership collocations. A bare "合作" is a noun that also
        # appears in non-event wording ("AI 合作模式"), so only verbs that bind it
        # assert a partnership. The "与…合作" pattern is handled separately.
        "建立合作", "开展合作", "进行合作", "结为合作", "成为合作",
    ),
    "appointment": (
        "任命", "晋升", "appoint", "appointed", "appoints", "appointing", "promoted",
    ),
    "departure": (
        "宣布离职", "宣布辞职", "announces departure", "announced departure", "departs", "departed", "离职", "辞职", "离开", "卸任", "leaving", "leaves", "left", "takes off",
        "headed out the door", "steps down", "resigns", "resigned",
    ),
    "organizational_change": (
        "解散", "disband", "disbanded", "disbands",
        # Establishing a new org/institute is an asserted organizational action.
        "establishes", "established", "founds", "founded", "sets up", "expands into",
    ),
    "layoff": ("裁员", "layoffs", "laid off", "cuts jobs"),
    "policy": (
        "颁布禁令", "出台禁令", "发布禁令", "监管裁决", "bans", "banned",
        "prohibits", "regulated", "issues a ban", "court rules", "court win",
        "call for action", "联合呼吁",
        # Rulings, disclosures and platform suspensions are asserted actions.
        "lose fight", "loses fight", "lost fight", "lose bid", "suspends", "suspended",
        "suspending", "reveal", "reveals", "revealed", "revealing",
        "unveils", "unveiled", "unveiling",
    ),
    "litigation": (
        "起诉", "提起诉讼", "提告", "状告", "被起诉", "指控",
        "sue", "sues", "sued", "suing", "lawsuit", "lawsuits", "files suit",
        "file suit", "filed suit", "files a lawsuit", "filed a lawsuit",
        "take to court", "takes to court", "took to court", "taken to court",
        "takes openai to court", "legal action", "alleging",
    ),
    "infrastructure": (
        "建设", "部署", "扩建", "扩大", "builds", "built", "deploys", "deployed",
        "deploying", "expands", "expanding", "building", "integrating",
        "opening", "rolling out",
    ),
    "security": (
        "披露漏洞", "发现漏洞", "修复漏洞", "discloses", "disclosed",
        "disclosing", "discovers", "discovered", "discovering",
        "fixes", "fixed", "fixing",
        # Breach/incident reporting is an asserted security event.
        "hacked", "hacks", "breached", "breach", "break into", "broke into",
        "breaks into", "details", "detailed", "披露", "泄露",
        "caught", "catches", "found", "finds",
        # "发现" only asserts a security event when bound to a security object;
        # a bare 发现 is ordinary narration ("我发现…") and must not frame an
        # action, or unrelated posts would look like security incidents during
        # duplicate detection.
        "发现并修复", "发现并披露", "发现安全", "发现攻击", "发现入侵",
        "发现异常", "发现问题", "被发现", "遭发现",
        # Chinese breach verbs the content LLM emits for security incidents.
        # "黑客" is a subject descriptor, not an action: listing it here would make
        # "黑客使用 X 入侵 Y" frame with no subject before the action.
        "入侵", "攻击", "攻破", "侵入", "黑入",
    ),
    "joining": (
        "入职", "加入", "joins", "joined", "joining", "hired", "hiring", "is now at",
    ),
    "open_source": (
        "开源", "open source", "open-source", "open-sources", "open-sourcing",
    ),
}

_ORGANIZATION_ALIASES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "google deepmind": "google deepmind",
    "deepmind": "google deepmind",
    "mistral": "mistral ai",
    "mistral ai": "mistral ai",
    "microsoft": "microsoft",
    "meta": "meta",
    "nvidia": "nvidia",
    "hugging face": "hugging face",
    "qwencloud": "qwencloud",
    "xai": "xai",
    "x.ai": "xai",
    "cohere": "cohere",
    "cerebras": "cerebras",
    "谷歌": "google",
    "微软": "microsoft",
    "英伟达": "nvidia",
}
_KNOWN_SURFACE_ENTITIES = (
    "claude",
    "opencode go",
    "pentagon",
)
_GENERIC_SUBJECTS = {
    "a company", "an ai company", "company", "the company", "this company",
    "team", "the team", "organization", "某公司", "一家公司", "多家公司",
    "该公司", "这家公司", "公司", "某机构", "该机构", "机构", "某团队",
    "该团队", "这个团队", "团队", "实验室", "我们", "我", "其", "它",
}
_GENERIC_DETAILS = {
    "ai", "model", "models", "product", "products", "strategy", "update",
    "模型", "产品", "战略", "计划", "进展", "动态", "消息", "更新", "研究",
    "能力", "功能", "新模型", "新产品", "全新模型", "全新产品",
}
_NON_NEWS_PATTERNS = (
    r"\bhow\b.*\bworks?\b",
    r"\bguide\b",
    r"\btutorial\b",
    r"\bmentions?\b",
    r"工作原理",
    r"使用指南",
    r"教程",
    r"提及",
    r"战略$",
    r"趋势$",
)
_METADATA_PATTERNS = (
    re.compile(r"\bPoints:\s*\d+", re.I),
    re.compile(r"#\s*Comments:\s*\d+", re.I),
    re.compile(r"\bComments:\s*\d+", re.I),
)
_NEGATION = re.compile(r"\b(?:not|never|without|would not)\b|未|没有|并未|不会")
# Planned/conditional framing: `may sue` / `considering suing` are not asserted actions.
_PLANNED_ACTION = re.compile(
    r"\b(?:may|might|could|would|should|considering|consider|plans?\s+to|planning\s+to|"
    r"intends?\s+to|expected\s+to|set\s+to|threatens?\s+to|threatened\s+to|"
    r"weighing|mulls?|mulling|poised\s+to|about\s+to|likely\s+to|rumou?red\s+to)\b"
    r"|可能|或将|拟|计划|打算|考虑|预计将|有望"
)
_SOURCE_ACTION_TRANSLATIONS = {
    "release": "发布",
    "released": "发布",
    "releases": "发布",
    "launch": "发布",
    "launched": "发布",
    "launches": "发布",
    "available": "可用",
    "is live": "上线",
    "releasing": "发布",
    "rolling out": "上线",
    "leaves": "离职",
    "left": "离职",
    "departs": "离职",
    "departed": "离职",
    "court rules": "法院裁定",
    "court win": "法院裁决",
    "in court": "法院裁决",
    "update": "更新",
    "updated": "更新",
    "updates": "更新",
    "upgrade": "升级",
    "upgraded": "升级",
    "upgrades": "升级",
    "pause": "暂停",
    "paused": "暂停",
    "pauses": "暂停",
    "track": "追踪",
    "tracks": "追踪",
    "reduce": "减少",
    "reduced": "减少",
    "reduces": "减少",
    "improve": "提升",
    "improved": "提升",
    "exceed": "超过",
    "exceeded": "超过",
    "jump": "增长",
    "jumps": "增长",
    "disband": "解散",
    "disbanded": "解散",
    "disbands": "解散",
    "raise": "融资",
    "raises": "融资",
    "raised": "融资",
    "acquire": "收购",
    "acquired": "收购",
    "acquires": "收购",
    "appoint": "任命",
    "appointed": "任命",
    "appoints": "任命",
    "join": "加入",
    "joins": "加入",
    "joined": "加入",
    "hire": "入职",
    "hired": "入职",
    "court rules": "法院裁定",
    "court win": "法院裁决",
    "call for action": "联合呼吁",
    "is now at": "加入",
    "establishes": "成立",
    "established": "成立",
    "founds": "成立",
    "founded": "成立",
}
# Cross-language news nouns that are ordinary translations, not fabrications.
# A Chinese term is only accepted when at least one of its English counterparts
# actually occurs in the bound source quote; that keeps the anti-fabrication
# guarantee (a noun the source never mentions still fails). Entities, people,
# model names and numbers must never be listed here: they must keep bindable
# Latin anchors instead. This is the single shared table for both the
# deterministic fallback title and the cross-language display binding.
CROSS_LANGUAGE_NOUN_EQUIVALENTS = {
    "研究院": ("institute",),
    "研究所": ("institute", "lab", "laboratory"),
    "实验室": ("lab", "laboratory"),
    "研究中心": ("research center", "research centre"),
    "辩论": ("debate",),
    "合作伙伴关系": ("partnership",),
    "合作伙伴": ("partner", "partners"),
    "合作": ("partnership", "partner", "partners", "collaboration", "collaborates"),
    "代理": ("agent", "agents"),
    "智能体": ("agent", "agents"),
    "功能": ("feature", "features", "functionality"),
    "能力": ("capability", "capabilities", "ability"),
    "云端": ("cloud",),
    "管理": ("manage", "manages", "management", "managing"),
    "模型": ("model", "models"),
    "行为": ("behavior", "behaviour", "behaviors"),
    "不良行为": ("bad behavior", "bad behaviour", "misbehavior", "misbehaviour"),
    "安全": ("safety", "security"),
    "开放权重": ("open-weight", "open weight"),
    "权重": ("weight", "weights"),
    "成立": ("launches", "launch", "establishes", "founds", "sets up", "forms"),
    "设立": ("establishes", "founds", "sets up", "forms"),
    "重新推出": ("relaunches", "relaunch"),
    "重启": ("relaunch", "relaunches", "restarts", "restart"),
    "拓宽": ("widen", "broaden", "broadens"),
    "成本": ("cost", "costs"),
    "价格": ("price", "prices", "pricing"),
    "音频": ("audio",),
    "训练": ("training", "train", "trains", "trained"),
    "推理": ("inference", "reasoning"),
    "性能": ("performance",),
    "速度": ("speed", "latency", "throughput"),
    "数据": ("data", "dataset", "datasets"),
    "用户": ("user", "users"),
    "企业": ("enterprise", "enterprises", "company", "companies"),
    "团队": ("team", "teams"),
    "研究": ("research",),
    "研究团队": ("research team",),
    "项目": ("project", "projects"),
    "平台": ("platform", "platforms"),
    "工具": ("tool", "tools"),
    "插件": ("plugin", "plugins"),
    "服务": ("service", "services"),
    "芯片": ("chip", "chips", "gpu", "gpus"),
    "基础设施": ("infrastructure",),
    "事件": ("incident", "incidents", "event", "events"),
    "事故": ("incident", "incidents"),
    "系统": ("system", "systems"),
    "报告": ("report", "reports"),
    "调查": ("investigation", "probe", "inquiry"),
    "诉讼": ("lawsuit", "litigation", "suit"),
    "监管": ("regulator", "regulation", "regulatory"),
    "禁令": ("ban", "bans"),
    "发布": ("release", "releases", "launch", "launches", "launched"),
    "推出": ("release", "releases", "launch", "launches", "rolls out", "roll out"),
    "上线": ("available", "live", "launches", "releases"),
    "更新": ("update", "updates", "updated"),
    "升级": ("upgrade", "upgrades", "upgraded"),
    "收购": ("acquire", "acquires", "acquisition", "buys"),
    "融资": ("raise", "raises", "funding", "funded"),
    "投资": ("invest", "invests", "investment", "funding"),
    "裁员": ("layoff", "layoffs", "laid off", "cuts jobs"),
    "离职": ("leaves", "left", "departs", "departed", "resigns", "resigned"),
    "辞职": ("resigns", "resigned", "steps down"),
    "加入": ("joins", "joined", "hired"),
    "起诉": ("sue", "sues", "sued", "suing", "lawsuit", "files suit", "filed suit"),
    "指控": ("alleging", "alleges", "accuses", "accused"),
    "裁定": ("court rules", "ruling", "rules"),
    "裁决": ("court win", "ruling", "verdict"),
    "入侵": ("break into", "broke into", "breached", "hack", "hacked"),
    "攻破": ("break into", "broke into", "breached"),
    "黑客": ("hacker", "hackers"),
    "攻击": ("attack", "attacks", "hacked"),
    "漏洞": ("vulnerability", "vulnerabilities", "flaw", "flaws"),
    "泄露": ("leak", "leaked", "leaks", "breach"),
    "披露": ("discloses", "disclosed", "reveals", "revealed", "details", "detailed"),
    "开源": ("open source", "open-source", "open-sources"),
    "部署": ("deploys", "deployed", "deployment"),
    "效率": ("efficiency", "efficient"),
    "论文": ("paper", "papers"),
    "研究论文": ("research paper",),
    "基准": ("benchmark", "benchmarks"),
    "榜单": ("leaderboard", "ranking", "rankings"),
    "排名": ("rank", "ranks", "ranked", "ranking"),
    # Remaining common renderings observed from the content LLM.
    "隐藏": ("hide", "hides", "hidden", "conceal", "conceals"),
    "留纸条": ("leaving notes", "leaves notes", "left notes"),
    "留下": ("leaving", "leaves", "left"),
    "纸条": ("note", "notes"),
    "后续": ("successors", "successor", "next"),
    "承担": ("leads", "lead", "takes on", "undertakes"),
    "四分之一": ("quarter",),
    "构建": ("building", "builds", "build", "developing"),
    "下一代": ("next-generation", "next generation", "next"),
    "工作": ("work", "works", "working"),
    "少部分": ("fraction",),
    "这部分": ("portion",),
    "多数": ("majority",),
    "现在": ("now", "currently"),
    # Terms observed in real translated headlines whose English counterparts
    # appear in the bound source sentence.
    "测试": ("testing", "test", "tests", "tested"),
    "安全测试": ("security testing",),
    "期间": ("during",),
    "意外": ("accidentally", "accidental"),
    "失控": ("rogue", "went rogue", "out of control"),
    "隐瞒": ("hid", "hide", "hides", "hidden", "conceal"),
    "此事": ("it", "this", "the incident"),
    "解决": ("solving", "solve", "solves", "solved"),
    "实际问题": ("real-world problems", "real world problems"),
    "工程": ("engineering", "engineer", "engineering challenges"),
    "挑战": ("challenges", "challenge"),
    "赋能": ("empower", "empowers", "empowering", "enable", "enables"),
    "科学家": ("scientists", "scientist"),
    "能源": ("energy",),
    "地球健康": ("planetary health", "earth health", "global health"),
    "重大": ("major", "significant", "grand"),
    "提速": ("speeds up", "faster", "accelerate"),
    "延迟": ("latency",),
    "吞吐": ("throughput",),
    "吞吐量": ("throughput",),
    "测量": ("measure", "measures", "measuring", "measurement"),
    "流量": ("traffic",),
    "增加": ("increasing", "increase", "increases", "increased"),
    "速度": ("speed", "faster", "velocity"),
    "比以往更快": ("faster than ever", "faster than before"),
    "典范": ("example", "exemplar"),
    "问题": ("problem", "problems"),
    "短语": ("phrase", "phrases"),
    "发表": ("published", "publishes", "publishing"),
    "论文": ("paper", "papers"),
    "暂停": ("moratorium", "pause", "pauses", "paused"),
    "实施": ("impose", "imposes", "implement", "implements"),
    "帮助": ("helps", "help", "helping", "assists"),
    "改进": ("improve", "improves", "improving", "improved"),
    "尝试": ("attempts", "attempt", "tries", "tried"),
    "做梦": ("dreaming", "dream", "dreams"),
}
_ATTRIBUTION_MARKERS = (
    "称",
    "分享",
    "表示",
    "帖子",
    "发文",
    "透露",
    "据",
    "研究者",
    "开发者",
    "媒体",
)
# Controlled Chinese markers allowed in a cross-language display claim. Derived
# here, next to EVENT_ACTION_MARKERS, so the vocabulary has a single source of
# truth and cannot drift between classification, binding and validation.
CROSS_LANGUAGE_RULE_ONLY_MARKERS = tuple(
    sorted(
        {
            *(
                marker
                for markers in EVENT_ACTION_MARKERS.values()
                for marker in markers
                if any("\u4e00" <= char <= "\u9fff" for char in marker)
            ),
            *_ATTRIBUTION_MARKERS,
            "公司", "厂商", "平台", "实验室", "团队", "机构", "模型", "产品",
            "工具", "系统", "服务", "项目", "版本", "该", "其", "一个", "一款",
            "于", "年", "并", "与", "和", "的", "了", "已", "已经", "将", "在",
            "为", "向", "由", "新", "正式", "完成", "周", "内", "开发者", "使用", "后",
            # Grammatical connectives that carry no factual claim of their own.
            "以", "及", "等", "中", "上", "对", "从", "到", "会", "可", "能", "正",
            "被", "把", "让", "使", "又", "也", "都", "还", "很", "更", "最", "多",
            "个", "次", "项", "条", "款", "种", "类", "时", "日", "月", "前", "后",
            "给", "于", "自", "用", "着", "过", "并", "且", "而", "则", "即", "如",
            # Temporal/coordinating connectives ("同时将", "并同时"). These carry no
            # factual claim, but a missing 同 left "同时将" as an unmatched residual
            # and rejected a correct translation ("... 同时将 AI 音频价格降低 ...").
            "同时", "同",
            # Numerals/quantifiers that only restate a source number.
            "一", "二", "两", "三", "四", "五", "六", "七", "八", "九", "十",
            "百", "千", "万", "亿", "些", "名", "家", "位", "份", "组", "批",
            # Degree/extent adverbs that modify the source number rather than
            # adding a claim ("降低高达 95%" / "降低最多 95%"). The number itself
            # stays a detail anchor; only the modifier is a controlled marker.
            # A missing 高达 left the correct Alibaba title as an unmatched
            # residual and forced a degraded fallback title.
            "高达", "最多", "低至", "大幅",
        },
        key=len,
        reverse=True,
    )
)
_GENERIC_PRODUCT_TOKENS = {
    "a", "an", "ai", "ai-powered", "api", "app", "code", "model", "new", "platform",
    "product", "service", "system", "the", "tool",
    # Generic AI concepts/acronyms, never a source-declared product token. Without
    # these, ``_single_protected_product_token`` mistakes ``AGI``/``LLM`` etc. in
    # phrases like "launches institute to widen the AGI debate" for a released
    # product and fabricates "发布 AGI".
    "agi", "asi", "llm", "gpu", "tpu", "cpu", "ml", "rl",
}
_AI_AGENT_DEPLOYMENT = re.compile(
    r"\bdeployed\b.*?\bin\s+(?P<duration>\d+)\s+weeks?\s+by\s+"
    r"(?P<agent>[A-Za-z][A-Za-z0-9.+-]*)\b",
    re.IGNORECASE,
)
_DEVELOPER_LLM_DEPARTURE = re.compile(
    r"^(?P<subject>[A-Za-z][A-Za-z0-9.+-]*)\s+developer\s+resigns\s+after\s+"
    r".*?\bLLM\s+use\b",
    re.IGNORECASE,
)
# A source-fallback subject must be a bound surface, not a prose fragment. This
# rejects `Nando de Freitas: Today we're` style residuals: attribution prefixes,
# possessives/contractions, clause punctuation and ordinary English function words.
_RESIDUAL_PROSE_SUBJECT = re.compile(
    r"(?:[:\u2014]|--|\bwe['\u2019]?(?:re|ve|d|ll)\b|['\u2019]s\b|"
    r"\b(?:today|yesterday|tomorrow|now|here|there|this|that|these|those|"
    r"the|a|an|we|our|us|they|their|it|its|he|she|his|her|i|you|your)\b)",
    re.IGNORECASE,
)
_UPDATE_RESULT_RELATION = re.compile(
    r"\b(?:scores?|reaches?|rank(?:s|ed)?|places?|improves?|improved|"
    r"increases?|increased|decreases?|decreased|higher|lower|faster|slower|"
    r"outperforms?|beats?|achieves?|achieved)\b|"
    r"得分|达到|提升|降低|高出|低于|加快|减少|超过|排名|位列",
    re.IGNORECASE,
)
_UPDATE_DIRECTION_PATTERNS = {
    "higher": re.compile(
        r"\b(?:improves?|improved|increases?|increased|higher|faster|"
        r"outperforms?|beats?)\b|提升|高出|加快|超过|快于",
        re.IGNORECASE,
    ),
    "lower": re.compile(
        r"\b(?:decreases?|decreased|lower|slower)\b|降低|低于|减少|慢于",
        re.IGNORECASE,
    ),
}
_UPDATE_VALUE_RELATION = re.compile(
    r"\b(?:scores?|reaches?|achieves?|achieved|rank(?:s|ed)?|places?)\b|"
    r"得分|达到|排名|位列",
    re.IGNORECASE,
)
_UPDATE_DIMENSION_PATTERNS = (
    ("score", re.compile(r"\b(?:scores?|scoring)\b|得分|分数", re.IGNORECASE)),
    ("latency", re.compile(r"\blatency\b|延迟", re.IGNORECASE)),
    (
        "speed",
        re.compile(
            r"\b(?:speed|throughput|faster|slower|tok/s|tokens/s)\b|"
            r"速度|吞吐|快于|慢于",
            re.IGNORECASE,
        ),
    ),
    (
        "rank",
        re.compile(
            r"\b(?:rank(?:s|ed)?|places?)\b|排名|位列|第\s*\d+\s*名",
            re.IGNORECASE,
        ),
    ),
)
_UPDATE_MECHANICAL_PROGRESS = re.compile(
    r"\b\d+(?:\.\d+)?\s*%|#\s*\d+\b|\b(?:rank|排名)\s*#?\s*\d+|"
    r"\b\d+(?:\.\d+)?\s*(?:x|ms|s|tok/s|tokens/s)\b|"
    r"\b(?:speed|latency|速度|延迟)\s*\d+|第\s*\d+\s*名",
    re.IGNORECASE,
)
_UPDATE_METRIC_STOPWORDS = {
    "a", "an", "and", "by", "for", "higher", "lower", "more", "on",
    "than", "the", "to", "with", "benchmark", "evaluation", "result",
}
_UPDATE_BEHAVIOR_PATTERNS = (
    (
        "demo",
        re.compile(r"\b(?:demonstrates?|shows?|tests?|tested)\b|展示|演示|测试", re.I),
    ),
    (
        "support",
        re.compile(r"\b(?:supports?|enables?|allows?|lets?)\b|支持|允许|可用于", re.I),
    ),
    ("generate", re.compile(r"\b(?:generates?|creates?)\b|生成|创建", re.I)),
    ("run", re.compile(r"\b(?:runs?|executes?)\b|运行|执行", re.I)),
    ("handle", re.compile(r"\b(?:handles?|processes?)\b|处理", re.I)),
)
_UPDATE_CAPABILITY_PATTERNS = (
    ("video", re.compile(r"\bvideos?\b|视频", re.I)),
    ("image", re.compile(r"\bimages?\b|图像|图片", re.I)),
    ("audio", re.compile(r"\baudio\b|音频", re.I)),
    ("code", re.compile(r"\bcode\b|代码", re.I)),
    ("agent", re.compile(r"\bagents?\b|智能体", re.I)),
    ("workflow", re.compile(r"\bworkflows?\b|工作流", re.I)),
    ("browser", re.compile(r"\bbrowsers?\b|浏览器", re.I)),
    ("control", re.compile(r"\bcontrol\b|控制", re.I)),
    ("document", re.compile(r"\b(?:documents?|files?)\b|文档|文件", re.I)),
)
_UPDATE_KNOWN_SUBJECT = re.compile(
    r"\b(?:gpt|chatgpt|claude(?:\s+code)?|gemini|llama|qwen|deepseek|mistral)"
    r"[\w.+/-]*(?:\s+(?-i:[A-Z])[\w.+/-]*)?\b",
    re.I,
)


_ACTION_PAIR_PATTERNS = (
    # "X 与 Y 合作" asserts a partnership; the bare noun "合作" alone does not
    # ("AI 合作模式"), so the "与 … 合作" shape is matched as a pair.
    (
        "partnership",
        re.compile(r"与[^，。；！？\n]{1,40}?合作|同[^，。；！？\n]{1,40}?合作"),
    ),
)


@dataclass(frozen=True, slots=True)
class PublishabilityResult:
    accepted: bool
    reason_codes: tuple[str, ...]
    event_type: str = ""
    subject_anchors: tuple[str, ...] = ()
    title_completeness: str = "incomplete"
    detail_anchors: tuple[str, ...] = ()
    # Bounded private sub-reason for otherwise-indistinguishable rejections.
    # Never a public contract: it disambiguates ``non_news_content`` into
    # ``instructional_content`` (a non-news title pattern matched) versus
    # ``no_asserted_action`` (no asserted event action could be framed).
    rejection_detail: str = ""


@dataclass(frozen=True, slots=True)
class _ClaimFrame:
    actions: frozenset[str]
    subjects: frozenset[str]
    details: frozenset[str]


@dataclass(frozen=True, slots=True)
class _UpdateClaimFrame:
    subjects: frozenset[str]
    details: frozenset[str]
    relations: frozenset[str]


def _normalize(value: str) -> str:
    return re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", unescape(value))
    ).strip()


def _contains_marker(value: str, marker: str) -> bool:
    if any("\u4e00" <= char <= "\u9fff" for char in marker):
        return marker in value
    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])",
        value,
        flags=re.I,
    ))


def asserted_action_types(value: str) -> frozenset[str]:
    normalized = _normalize(value).casefold()
    if _NEGATION.search(normalized):
        return frozenset()
    if _PLANNED_ACTION.search(normalized):
        return frozenset()
    actions = {
        action
        for action, markers in EVENT_ACTION_MARKERS.items()
        if any(_contains_marker(normalized, marker) for marker in markers)
    }
    actions.update(
        action
        for action, pattern in _ACTION_PAIR_PATTERNS
        if pattern.search(value)
    )
    return frozenset(actions)


def _first_action(value: str) -> tuple[int, int, str]:
    normalized = _normalize(value)
    lowered = normalized.casefold()
    matches: list[tuple[int, int, str]] = []
    for action, markers in EVENT_ACTION_MARKERS.items():
        for marker in markers:
            if any("\u4e00" <= char <= "\u9fff" for char in marker):
                index = lowered.find(marker.casefold())
                if index >= 0:
                    matches.append((index, index + len(marker), action))
            else:
                match = re.search(
                    rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])",
                    lowered,
                    flags=re.I,
                )
                if match:
                    matches.append((match.start(), match.end(), action))
    for action, pattern in _ACTION_PAIR_PATTERNS:
        match = pattern.search(value)
        if match:
            marker = match.group(0)
            index = lowered.find(_normalize(marker).casefold())
            if index >= 0:
                matches.append((index, index + len(_normalize(marker)), action))
    return min(matches, default=(-1, -1, ""), key=lambda item: item[0])


def _organization_anchors(value: str) -> set[str]:
    lowered = _normalize(value).casefold()
    return {
        f"org:{canonical}"
        for alias, canonical in _ORGANIZATION_ALIASES.items()
        if _contains_marker(lowered, alias)
    }


_MODEL_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:chatgpt(?![a-z0-9])|"
    r"(?:gpt|claude|gemini|llama|qwen|deepseek|model|mistral|grok|kimi|glm|"
    r"ernie|hunyuan|doubao|minimax|step|phi|command)"
    r"(?:[- ]?[a-z]+){0,2}[- ]?\d[\w.+-]*)"
    r"(?:\s+(?:flash|mini|pro|ultra|ultrafast|preview|turbo|max|nano|opus|haiku|sonnet))?",
    re.I,
)


def _model_anchors(value: str) -> set[str]:
    return {
        "model:" + re.sub(
            r"\s+", "-", match.group(0).casefold().rstrip(".,;:!?，。；：！？")
        )
        for match in _MODEL_PATTERN.finditer(_normalize(value))
    }


def _surface_anchor_matches(value: str) -> tuple[str, ...]:
    normalized = _normalize(value)
    matches: list[tuple[int, str]] = []
    for alias in _KNOWN_SURFACE_ENTITIES:
        match = re.search(
            rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
            normalized,
            flags=re.I,
        )
        if match:
            matches.append((match.start(), match.group(0)))
    for alias in sorted(_ORGANIZATION_ALIASES, key=len, reverse=True):
        match = re.search(
            rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
            normalized,
            flags=re.I,
        )
        if match:
            matches.append((match.start(), match.group(0)))
    matches.extend(
        (match.start(), match.group(0))
        for match in _MODEL_PATTERN.finditer(normalized)
    )
    return tuple(value for _, value in sorted(matches, key=lambda item: item[0]))


# Capitalised words that are never a source-declared product/model/org: bare
# geography, nationalities and ordinary sentence nouns. Without this, the
# deterministic English fallback turns any capitalised common noun after the
# verb into a fabricated release object -- "Model Vault is now available in
# Canada" must not become "Cohere 可用 Canada".
_NON_PRODUCT_PROPER_NOUNS = {
    "africa", "america", "asia", "australia", "australian", "austria",
    "belgium", "brazil", "britain", "british", "california", "canada",
    "canadian", "china", "chinese", "denmark", "europe", "european", "france",
    "germany", "german", "india", "indian", "ireland", "israel", "italy",
    "japan", "japanese", "korea", "london", "mexico", "netherlands",
    "norway", "paris", "poland", "portugal", "russia",
    "singapore", "spain", "sweden", "switzerland", "taiwan", "texas", "tokyo",
    "uk", "usa", "york",
    # Weekday/month and generic sentence nouns that survive capitalisation.
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday",
    "for", "from", "with", "and", "the", "this", "that", "those", "these",
    "it", "its", "they", "their", "we", "our", "you", "your", "he", "she",
    "his", "her", "now", "today", "tomorrow", "yesterday", "here", "there",
    "more", "most", "some", "any", "all", "both", "each", "every", "other",
}


def _single_protected_product_token(value: str) -> str | None:
    """Return one source-declared product token, never generic English prose."""
    match = re.search(r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9.+-]{2,}", value)
    if match is None:
        return None
    token = match.group(0)
    folded = token.casefold()
    if folded in _GENERIC_PRODUCT_TOKENS:
        return None
    # A bare place/demonym/time word is a sentence noun, not a released object;
    # only registered orgs, models or explicit product-style tokens may serve as
    # the fallback detail. Checking only the first capitalised token is
    # deliberate: skipping past a rejected one would walk deeper into prose and
    # fabricate a detail from an unrelated later name (e.g. `"true North"`).
    if folded in _NON_PRODUCT_PROPER_NOUNS:
        return None
    return token


def source_anchored_title(source: SourceEvidence) -> str | None:
    """Build a minimal cross-language title solely from known source anchors."""
    title = _normalize(source.source_title)
    if not title or any("\u4e00" <= char <= "\u9fff" for char in title):
        return None
    developer_departure = _DEVELOPER_LLM_DEPARTURE.match(title)
    if developer_departure:
        return f"{developer_departure.group('subject')} 开发者在 LLM 使用后辞职"
    agent_deployment = _AI_AGENT_DEPLOYMENT.search(title)
    if agent_deployment:
        return (
            f"{agent_deployment.group('agent')} 在 "
            f"{agent_deployment.group('duration')} 周内完成部署"
        )
    action_matches = [
        (match.start(), match.end(), translation)
        for marker, translation in _SOURCE_ACTION_TRANSLATIONS.items()
        for match in [
            re.search(
                rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])",
                title,
                flags=re.I,
            )
        ]
        if match
    ]
    if not action_matches:
        return None
    start, end, action = min(action_matches, key=lambda item: item[0])
    after = title[end:]
    subjects = _surface_anchor_matches(title[:start])
    details = _surface_anchor_matches(after)
    subject = subjects[0] if subjects else _literal_subject_surface(title[:start])
    if not subject:
        return None
    # A detail anchor must be the verb's object, not a partnership/list companion.
    # "Base Labs launches ... partnership with Hugging Face and Goodfire" must not
    # become "Base Labs 发布 Hugging Face": the org is introduced by "with"/"and",
    # not released. Reject any anchor preceded by a coordinating/prepositional
    # connector that marks it as a companion rather than the object.
    detail = None
    for anchor in details:
        if anchor.casefold() == subject.casefold():
            continue
        anchor_start = after.casefold().find(anchor.casefold())
        if anchor_start >= 0 and re.search(
            r"\b(?:with|and)\b\s*$", after[:anchor_start], flags=re.I
        ):
            continue
        # A comparison/prepositional target is not the verb's object either.
        # "xAI launches Grok 4.7 ..., but benchmarks reveal a wide gap to Claude"
        # must not yield "xAI 发布 Claude": Claude is introduced by "to" as the
        # thing being compared against, not released.
        if anchor_start >= 0 and re.search(
            r"\b(?:to|than|vs\.?|versus|over|against|compared\s+(?:to|with))\b\s*$",
            after[:anchor_start],
            flags=re.I,
        ):
            continue
        detail = anchor
        break
    if detail is None:
        # Version tokens outrank ``@handle``: in ``nexus-agents@8.101.0`` the tag
        # is a version, and the loose ``@\\w+`` handle pattern would otherwise
        # truncate it to ``@8`` and publish "nexus-agents 发布 @8". A handle is
        # only a handle when it stands alone, not when glued to a preceding word.
        safe_detail = re.search(
            r"(?<![A-Za-z0-9])v?\d+(?:\.\d+)+(?![A-Za-z0-9])|"
            r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]+|"
            r"(?:\$\s*)?\d+(?:[.,]\d+)?\s*(?:%|x|ms|s|tok/s|tokens/s|billion|million)\b",
            after,
            flags=re.I,
        )
        detail = safe_detail.group(0) if safe_detail else None
    if detail is None:
        # Fall back to a single protected product token only when it is the verb's
        # object, not a partnership/list companion. "launches ... partnership with
        # Hugging Face and Goodfire" must not yield "发布 Hugging".
        token = _single_protected_product_token(after)
        if token is not None:
            token_start = after.casefold().find(token.casefold())
            if token_start < 0 or not re.search(
                r"\b(?:with|and)\b\s*$", after[:token_start], flags=re.I
            ):
                detail = token
    return f"{subject} {action} {detail}" if detail else None


def _numeric_anchors(value: str) -> set[str]:
    return {
        "number:" + token.replace(",", "").casefold()
        for token in re.findall(r"(?:\$\s*)?\d+(?:[.,]\d+)?(?:\s*(?:%|gw|mw|亿美元|万元))?", value, re.I)
    }


def _literal_subject(value: str) -> str:
    subject = _normalize(value).strip(" ,，:：-—")
    subject = re.sub(r"^(?:在|于|截至)\s*", "", subject)
    subject = re.sub(r"\b(?:has|have|had|is|are|was|were|will)\s*$", "", subject, flags=re.I)
    # Drop trailing time adverbs that would otherwise survive as a prose fragment
    # subject ("Artificial intelligence now beats ..." -> "Artificial intelligence").
    subject = re.sub(
        r"\s+\b(?:now|today|yesterday|tomorrow|currently|recently|already)\b\s*$",
        "",
        subject,
        flags=re.I,
    ).strip(" ,，:：-—")
    if not subject or subject.casefold() in _GENERIC_SUBJECTS:
        return ""
    if re.fullmatch(r"\d+(?:[.,]\d+)?", subject):
        return ""
    return f"literal-subject:{subject.casefold()}"


def _literal_subject_surface(value: str) -> str:
    subject = _normalize(value).strip(" ,，:：-—")
    subject = re.sub(r"^(?:在|于|截至)\s*", "", subject)
    subject = re.sub(r"\b(?:has|have|had|is|are|was|were|will)\s*$", "", subject, flags=re.I)
    subject = re.sub(
        r"\s+\b(?:now|today|yesterday|tomorrow|currently|recently|already)\b\s*$",
        "",
        subject,
        flags=re.I,
    ).strip(" ,，:：-—")
    if not _literal_subject(subject):
        return ""
    # A fallback subject must be a mechanically bound surface, never residual
    # prose: reject source attribution prefixes, possessives, clause punctuation
    # and ordinary English function words that signal a sentence fragment.
    if _RESIDUAL_PROSE_SUBJECT.search(subject):
        return ""
    return subject


_COMPARISON_TARGET = re.compile(
    r"\b(?:to|than|vs\.?|versus|over|against|compared\s+(?:to|with))\s+"
    r"(?P<target>[A-Za-z][\w.+-]*(?:\s+(?:and|or)\s+[A-Za-z][\w.+-]*)*)",
    re.I,
)


def _comparison_target_tokens(value: str) -> set[str]:
    """Tokens that a comparison preposition introduces, e.g. `gap to Claude`.

    These name what the subject is measured against, never the released object,
    so they must not satisfy a display claim's detail requirement.
    """
    tokens: set[str] = set()
    for match in _COMPARISON_TARGET.finditer(_normalize(value)):
        for token in re.split(r"\s+(?:and|or)\s+", match.group("target")):
            cleaned = token.strip(".,;:!?，。；：！？").casefold()
            if cleaned:
                tokens.add(cleaned)
    return tokens


def _detail_anchors(value: str) -> set[str]:
    anchors = _organization_anchors(value) | _model_anchors(value) | _numeric_anchors(value)
    comparison_targets = _comparison_target_tokens(value)
    residual = _normalize(value).casefold()
    if re.search(r"\bllm\s*使用", residual, flags=re.IGNORECASE):
        anchors.update({"literal:llm", "literal:use"})
        residual = re.sub(r"\bllm\s*使用", " ", residual, flags=re.IGNORECASE)
    for markers in EVENT_ACTION_MARKERS.values():
        for marker in markers:
            residual = re.sub(re.escape(marker.casefold()), " ", residual)
    residual = re.sub(r"[^a-z0-9\u4e00-\u9fff.+-]+", " ", residual)
    for raw_token in residual.split():
        token = raw_token.strip(".,;:!?，。；：！？")
        if token not in _GENERIC_DETAILS and len(token) >= 2:
            anchors.add(f"literal:{token}")
    # Drop anchors that are only present as comparison targets.
    return {
        anchor
        for anchor in anchors
        if anchor.removeprefix("literal:") not in comparison_targets
        and not any(
            anchor.casefold().endswith(target) or target in anchor.casefold().split("-")
            for target in comparison_targets
        )
    }


def _claim_frame(value: str) -> _ClaimFrame | None:
    normalized = _normalize(value)
    start, end, action = _first_action(normalized)
    if start < 0 or not action:
        return None
    before = normalized[:start]
    after = normalized[end:]
    timed_deployment = re.fullmatch(
        r"(?P<subject>[A-Za-z][A-Za-z0-9.+-]*)\s*在\s*\d+\s*周内完成?",
        before,
    )
    developer_departure = re.fullmatch(
        r"(?P<subject>[A-Za-z][A-Za-z0-9.+-]*)\s*开发者在\s*"
        r"(?P<detail>LLM\s*使用)后",
        before,
        flags=re.IGNORECASE,
    )
    if timed_deployment:
        subject_text = timed_deployment.group("subject")
    elif developer_departure:
        subject_text = f"{developer_departure.group('subject')} developer"
    else:
        subject_text = before
    subjects = _organization_anchors(subject_text) | _model_anchors(subject_text)
    literal = _literal_subject(subject_text)
    if literal and not subjects:
        subjects.add(literal)
    agent_match = re.search(
        r"\bby\s+([A-Za-z][A-Za-z0-9.+-]*)\b",
        after,
        flags=re.IGNORECASE,
    )
    if agent_match:
        agent = _literal_subject(agent_match.group(1))
        if agent:
            subjects.add(agent)
    details = _detail_anchors(after)
    if timed_deployment:
        details.update(_numeric_anchors(before))
    if developer_departure:
        details.update(_detail_anchors(developer_departure.group("detail")))
    if action == "release":
        availability_detail = re.search(
            r"在\s*([A-Za-z][A-Za-z0-9.+/-]*(?:\s+[A-Za-z][A-Za-z0-9.+/-]*)*)\s*(?:中|上)?\s*可用",
            normalized,
        )
        if availability_detail:
            details.update(_detail_anchors(availability_detail.group(1)))
    return _ClaimFrame(frozenset({action}), frozenset(subjects), frozenset(details))


def _sentences(value: str) -> tuple[str, ...]:
    return tuple(
        part.strip()
        for part in re.split(r"[。！？!?;；\n]+|(?<=[a-z0-9])\.\s+", value, flags=re.I)
        if part.strip()
    )


def _publisher_subject(source: SourceEvidence) -> frozenset[str]:
    if not source.is_official or not source.official_identity_source:
        return frozenset()
    anchors = _organization_anchors(source.publisher_name)
    if anchors:
        return frozenset(anchors)
    literal = _literal_subject(source.publisher_name)
    return frozenset({literal}) if literal else frozenset()


def _frame_supported(
    display: _ClaimFrame,
    source: _ClaimFrame,
    *,
    publisher_subjects: frozenset[str] = frozenset(),
) -> bool:
    if not display.actions <= source.actions:
        return False
    available_subjects = source.subjects | publisher_subjects
    if not display.subjects or not display.subjects <= available_subjects:
        return False
    return bool(display.details and display.details <= source.details)


def claim_supported_by_quote(
    claim: str,
    quote: str,
    *,
    source: SourceEvidence | None = None,
) -> bool:
    display = _claim_frame(claim)
    if display is None:
        return False
    publisher_subjects = _publisher_subject(source) if source else frozenset()
    return any(
        frame is not None
        and _frame_supported(display, frame, publisher_subjects=publisher_subjects)
        for frame in (_claim_frame(sentence) for sentence in _sentences(quote))
    )


def _is_promotional_or_vague(title: str, evidence_text: str) -> bool:
    combined = _normalize(f"{title} {evidence_text}")
    return bool(
        x_content_rejection_reason({"summary": combined})
        or re.search(r"\b(?:interesting|trend)\b", title, flags=re.I)
        or any(re.search(pattern, title, flags=re.I) for pattern in _NON_NEWS_PATTERNS)
    )


def _first_update_relation(value: str) -> tuple[int, int, str]:
    normalized = _normalize(value)
    matches: list[tuple[int, int, str]] = []
    metric = _UPDATE_RESULT_RELATION.search(normalized)
    if metric:
        matches.append((metric.start(), metric.end(), "metric"))
    for relation, pattern in _UPDATE_BEHAVIOR_PATTERNS:
        match = pattern.search(normalized)
        if match:
            matches.append((match.start(), match.end(), f"behavior:{relation}"))
    return min(matches, default=(-1, -1, ""), key=lambda item: item[0])


def _update_subject_anchors(value: str, *, publisher_name: str = "") -> set[str]:
    normalized = _normalize(value)
    relation_start, _relation_end, _relation_type = _first_update_relation(normalized)
    subject_text = normalized[:relation_start] if relation_start >= 0 else ""
    # In comparison headlines, anchors after `vs`/`than` are comparison
    # objects, not alternate subjects for the displayed claim.
    if re.search(
        r"\b(?:vs\.?|versus|than|compared\s+with)\b|对比|相比|与",
        subject_text,
        flags=re.I,
    ):
        return set()
    if publisher_name:
        subject_text = re.sub(
            rf"^\s*{re.escape(_normalize(publisher_name))}\s*[:：]\s*",
            "",
            subject_text,
            flags=re.I,
        )
    anchors = _organization_anchors(subject_text) | _model_anchors(subject_text)
    anchors.update(
        f"entity:{re.sub(r'\s+', '-', match.group(0).casefold())}"
        for match in _UPDATE_KNOWN_SUBJECT.finditer(subject_text)
    )
    if anchors:
        return anchors
    for token in re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9.+/-]*", subject_text):
        letters = "".join(char for char in token if char.isalpha())
        if (
            any(char.isdigit() for char in token)
            or (letters.isupper() and len(letters) >= 2)
            or (any(char.isupper() for char in letters[1:]) and any(char.islower() for char in letters))
        ):
            anchors.add(f"entity:{token.casefold().rstrip('.,;:!?')}")
    return anchors


def _update_named_detail_anchors(value: str) -> set[str]:
    normalized = _normalize(value)
    _relation_start, relation_end, _relation_type = _first_update_relation(normalized)
    detail_text = normalized[relation_end:] if relation_end >= 0 else ""
    anchors: set[str] = set()
    for token in re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9.+/-]*", detail_text):
        stripped = token.rstrip(".,;:!?")
        letters = "".join(char for char in stripped if char.isalpha())
        if (
            "/" in stripped
            or (
                "-" in stripped
                and (
                    any(char.isdigit() for char in stripped)
                    or letters.isupper()
                )
            )
            or (letters.isupper() and len(letters) >= 3)
            or (
                any(char.isupper() for char in letters[1:])
                and any(char.islower() for char in letters)
            )
        ):
            anchors.add(f"named:{stripped.casefold()}")
    return anchors


def _update_metric_anchors(value: str) -> set[str]:
    normalized = _normalize(value)
    _relation_start, relation_end, relation_type = _first_update_relation(normalized)
    if relation_type != "metric":
        return set()
    detail_text = normalized[relation_end:]
    return {
        f"metric:{token.casefold()}"
        for token in re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9-]*", detail_text)
        if len(token) >= 3 and token.casefold() not in _UPDATE_METRIC_STOPWORDS
    }


def _update_detail_anchors(value: str) -> set[str]:
    normalized = _normalize(value)
    _relation_start, _relation_end, relation_type = _first_update_relation(normalized)
    if not relation_type:
        return set()
    named = _update_named_detail_anchors(normalized)
    metrics = _update_metric_anchors(normalized)
    capabilities = {
        f"capability:{capability}"
        for capability, pattern in _UPDATE_CAPABILITY_PATTERNS
        if pattern.search(normalized)
    }
    if relation_type == "metric":
        if not (_UPDATE_MECHANICAL_PROGRESS.search(normalized) or named or metrics):
            return set()
        anchors = _numeric_anchors(normalized)
        anchors.update(named | metrics)
        return anchors
    return named | capabilities


def _update_relation_types(value: str) -> set[str]:
    normalized = _normalize(value)
    _relation_start, _relation_end, relation_type = _first_update_relation(normalized)
    if relation_type.startswith("behavior:"):
        return {relation_type}
    if relation_type != "metric":
        return set()
    dimensions = {
        dimension
        for dimension, pattern in _UPDATE_DIMENSION_PATTERNS
        if pattern.search(normalized)
    }
    # Only dimensions with an explicit deterministic vocabulary may establish
    # a relation frame. Named evaluation details remain evidence anchors, not
    # metric dimensions (for example, Div-300 is a benchmark, not a metric).
    # Do not collapse unknown metric wording into a generic result dimension:
    # that would let an accepted claim swap accuracy, quality, or another
    # unregistered metric while preserving only the number and direction.
    directions = {
        direction
        for direction, pattern in _UPDATE_DIRECTION_PATTERNS.items()
        if pattern.search(normalized)
    }
    if not directions and _UPDATE_VALUE_RELATION.search(normalized):
        directions = {"value"}
    return {
        f"{dimension}:{direction}"
        for dimension in dimensions
        for direction in directions
    }


def _update_claim_frame(
    value: str,
    *,
    source: SourceEvidence,
) -> _UpdateClaimFrame:
    return _UpdateClaimFrame(
        frozenset(
            _update_subject_anchors(value, publisher_name=source.publisher_name)
        ),
        frozenset(_update_detail_anchors(value)),
        frozenset(_update_relation_types(value)),
    )


def update_claim_supported_by_quote(
    claim: str,
    quote: str,
    *,
    source: SourceEvidence,
) -> bool:
    """Require one bound quote to contain the complete AI-update claim frame."""
    display = _update_claim_frame(claim, source=source)
    if not display.subjects or not display.details or not display.relations:
        return False
    for sentence in _sentences(quote):
        evidence = _update_claim_frame(sentence, source=source)
        # A sentence containing multiple metric/direction pairs is ambiguous
        # under the deterministic frame model. Do not allow its cartesian
        # product to authorize a mismatched display claim.
        if len(evidence.relations) != 1:
            continue
        if (
            display.subjects <= evidence.subjects
            and display.details <= evidence.details
            and display.relations <= evidence.relations
        ):
            return True
    return False


def validate_update_source_publishability(
    source: SourceEvidence,
) -> PublishabilityResult:
    """Validate a concrete AI update without requiring a hard-news action."""
    title = _normalize(source.source_title)
    if _is_promotional_or_vague(title, source.evidence_text):
        return PublishabilityResult(False, ("update_missing_concrete_detail",))
    subjects = _update_subject_anchors(title, publisher_name=source.publisher_name)
    details = _update_detail_anchors(title)
    if not subjects:
        return PublishabilityResult(False, ("update_missing_subject",))
    if not details:
        return PublishabilityResult(False, ("update_missing_concrete_detail",))
    return PublishabilityResult(
        True,
        (),
        "ai_update",
        tuple(sorted(subjects)),
        "complete",
        tuple(sorted(details)),
    )


def validate_update_display_publishability(
    title: str,
    brief: str,
    source: SourceEvidence,
) -> PublishabilityResult:
    """Validate that a displayed AI update keeps source-bound concrete anchors."""
    normalized = _normalize(title)
    if _is_promotional_or_vague(normalized, source.evidence_text):
        return PublishabilityResult(False, ("update_missing_concrete_detail",))
    subjects = _update_subject_anchors(
        normalized,
        publisher_name=source.publisher_name,
    )
    details = _update_detail_anchors(normalized)
    if not subjects:
        return PublishabilityResult(False, ("update_missing_subject",))
    if not details:
        return PublishabilityResult(False, ("update_missing_concrete_detail",))

    source_quotes = (source.source_title, *_sentences(source.evidence_text))
    for claim in (normalized, *_sentences(brief)):
        if not any(
            update_claim_supported_by_quote(claim, quote, source=source)
            for quote in source_quotes
        ):
            return PublishabilityResult(False, ("update_claim_not_source_bound",))
    return PublishabilityResult(
        True,
        (),
        "ai_update",
        tuple(sorted(subjects)),
        "complete",
    )


def validate_source_publishability(source: SourceEvidence) -> PublishabilityResult:
    evidence = _normalize(source.evidence_text)
    title = _normalize(source.source_title)
    if source.channel == "github":
        lowered = evidence.casefold()
        activity_markers = ("star", "commit", "recent push", "近期活跃")
        publication_markers = ("release", "readme", "announcement", "发布说明")
        if any(marker in lowered for marker in activity_markers) and not any(
            marker in lowered for marker in publication_markers
        ):
            return PublishabilityResult(False, ("github_activity_only",))
    if source.discovered_via == "hacker_news" and any(
        pattern.search(evidence) for pattern in _METADATA_PATTERNS
    ):
        return PublishabilityResult(False, ("metadata_only_evidence",))
    if any(re.search(pattern, title, flags=re.I) for pattern in _NON_NEWS_PATTERNS):
        return PublishabilityResult(
            False,
            ("non_news_content",),
            rejection_detail="instructional_content",
        )
    frame = _claim_frame(title)
    if frame is None:
        return PublishabilityResult(
            False,
            ("non_news_content",),
            rejection_detail="no_asserted_action",
        )
    if not frame.subjects:
        return PublishabilityResult(False, ("source_missing_subject",))
    if not frame.details:
        return PublishabilityResult(False, ("source_missing_event_detail",))
    return PublishabilityResult(
        True,
        (),
        next(iter(frame.actions)),
        tuple(sorted(frame.subjects)),
        "complete",
    )


def validate_content_source_publishability(
    source: SourceEvidence,
) -> PublishabilityResult:
    """Dispatch source sufficiency by the immutable content type."""
    if source.content_type == "ai_update":
        return validate_update_source_publishability(source)
    if source.content_type == "attributed_opinion":
        if not (
            source.opinion_eligible
            and source.original_post
            and source.context_complete
            and source.opinion_author.strip()
        ):
            return PublishabilityResult(False, ("opinion_author_not_allowed",))
        return PublishabilityResult(True, (), "attributed_opinion")
    return validate_source_publishability(source)


def cross_language_counterpart_present(noun: str, quote_lower: str) -> bool:
    """Whether a Chinese news noun has a literal English counterpart in the quote."""
    return any(
        counterpart in quote_lower
        for counterpart in CROSS_LANGUAGE_NOUN_EQUIVALENTS.get(noun, ())
    )


def cross_language_unmatched_residual(
    claim: str,
    quote: str,
    allowed_markers: tuple[str, ...],
) -> str:
    """Return Chinese residual not covered by an allowed marker or a bound noun.

    Unlike a plain ``str.replace`` sweep, this walks the claim longest-match-first
    so a multi-character noun (「合作伙伴关系」) wins over a shorter one and is never
    split into fragments. A noun is consumed only when its English counterpart
    occurs in the quote; otherwise it stays residual, so fabricated nouns fail.
    """
    quote_lower = quote.lower()
    vocabulary = tuple(
        sorted(
            {*allowed_markers, *CROSS_LANGUAGE_NOUN_EQUIVALENTS},
            key=len,
            reverse=True,
        )
    )
    residual: list[str] = []
    index = 0
    while index < len(claim):
        char = claim[index]
        if not ("\u4e00" <= char <= "\u9fff"):
            index += 1
            continue
        matched = next(
            (word for word in vocabulary if claim.startswith(word, index)), None
        )
        if matched is None:
            residual.append(char)
            index += 1
            continue
        if matched in CROSS_LANGUAGE_NOUN_EQUIVALENTS and not (
            cross_language_counterpart_present(matched, quote_lower)
        ):
            residual.append(matched)
        index += len(matched)
    return "".join(residual)


def _cross_language_display_bound(claim: str, evidence_text: str) -> bool:
    """Whether a Chinese claim binds to one English source sentence.

    Used only as a fallback when the same-language literal subset rule cannot
    apply. Requires: Chinese display with English evidence, all Latin anchors
    drawn from that one sentence, and every remaining Chinese term either a
    controlled marker or a noun whose English counterpart occurs in the same
    sentence.
    """
    if not any("\u4e00" <= char <= "\u9fff" for char in claim):
        return False
    if any("\u4e00" <= char <= "\u9fff" for char in evidence_text):
        return False
    from src.briefing.validator import (  # noqa: PLC0415 (circular import)
        _cross_language_anchors,
    )

    claim_anchors = _cross_language_anchors(claim)
    return any(
        claim_anchors <= _cross_language_anchors(sentence)
        and not cross_language_unmatched_residual(
            claim,
            sentence,
            CROSS_LANGUAGE_RULE_ONLY_MARKERS,
        )
        and not _anchors_are_comparison_targets_only(claim_anchors, sentence)
        for sentence in _sentences(evidence_text)
        if _cross_language_anchors(sentence)
    )


def _anchors_are_comparison_targets_only(
    claim_anchors: frozenset[str] | set[str],
    sentence: str,
) -> bool:
    """Whether the claim's object anchor appears only as a comparison target.

    "xAI launches Grok 4.7 …, but benchmarks reveal a wide gap to Claude and
    GPT-6" must not bind "xAI 发布 Claude": `claude` is present in the sentence
    only after "to", naming what Grok is compared against. The subject anchor
    (`xai`) stays legitimate, so the check looks at the anchors that are *not*
    already explained as the sentence subject.
    """
    if not claim_anchors:
        return False
    comparison_targets = _comparison_target_tokens(sentence)
    if not comparison_targets:
        return False
    targeted = {
        anchor
        for anchor in claim_anchors
        if anchor.casefold() in comparison_targets
        or any(
            anchor.casefold() == target
            or anchor.casefold().startswith(f"{target}-")
            or anchor.casefold().endswith(f"-{target}")
            for target in comparison_targets
        )
    }
    if not targeted:
        return False
    # Subject anchors (appearing before the action verb) are explained by the
    # sentence frame, so only a non-subject, non-target anchor can stand in as
    # the released object.
    from src.briefing.validator import (  # noqa: PLC0415 (circular import)
        _cross_language_anchors,
    )

    start, _end, _action = _first_action(sentence)
    subject_anchors = {
        anchor.casefold()
        for anchor in _cross_language_anchors(sentence[:start] if start >= 0 else "")
    }
    object_anchors = {
        anchor for anchor in claim_anchors if anchor.casefold() not in subject_anchors
    }
    return bool(object_anchors) and object_anchors <= targeted


def validate_display_publishability(
    title: str,
    brief: str,
    source: SourceEvidence,
) -> PublishabilityResult:
    normalized = _normalize(title)
    frame = _claim_frame(normalized)
    if frame is None:
        return PublishabilityResult(False, ("title_missing_event_action",))
    if not frame.subjects:
        return PublishabilityResult(False, ("title_missing_subject",))
    if not frame.details:
        return PublishabilityResult(False, ("title_missing_event_detail",))
    if not claim_supported_by_quote(normalized, source.evidence_text, source=source):
        # Fall back to the cross-language equivalence path before rejecting. The
        # same-language literal detail-subset rule cannot apply when the display
        # is Chinese and the source is English, so bind via the shared
        # noun-equivalence table instead. Latin anchors must still come from the
        # source, any Chinese noun without a source counterpart stays residual
        # and is rejected, and every check runs per sentence so a claim cannot be
        # assembled from anchors that live in different source sentences.
        if _cross_language_display_bound(normalized, source.evidence_text):
            return PublishabilityResult(
                True,
                (),
                next(iter(frame.actions)),
                tuple(sorted(frame.subjects)),
                "complete",
            )
        source_actions = asserted_action_types(source.evidence_text)
        reason = (
            "title_action_not_source_bound"
            if not frame.actions <= source_actions
            else "title_claim_not_source_bound"
        )
        return PublishabilityResult(False, (reason,))
    return PublishabilityResult(
        True,
        (),
        next(iter(frame.actions)),
        tuple(sorted(frame.subjects)),
        "complete",
    )
