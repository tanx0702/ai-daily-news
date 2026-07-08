"""
Hugging Face 采集器

使用 HF Hub API 获取近期热门/更新的 AI 模型，
过滤文本生成、图像生成、多模态等核心 AI 类型。
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from math import log1p
from typing import Optional

import requests

from src.collectors import BaseCollector

logger = logging.getLogger(__name__)

HF_API_BASE = "https://huggingface.co/api"

# 核心 AI pipeline tags — 只有这些类型才入选
AI_PIPELINE_TAGS = {
    "text-generation", "text2text-generation", "image-generation",
    "video-generation", "text-to-image", "image-to-text",
    "text-to-video", "image-to-video",
    "automatic-speech-recognition", "text-to-speech",
    "multimodal", "visual-question-answering",
    "document-question-answering", "image-segmentation",
    "image-classification", "object-detection",
    "reinforcement-learning", "robotics",
}

# 管道标签 → 中文分类
TAG_CATEGORY = {
    "text-generation": "文本生成",
    "text2text-generation": "文本生成",
    "image-generation": "图像生成",
    "video-generation": "视频生成",
    "text-to-image": "文本到图像",
    "image-to-text": "图像理解",
    "text-to-video": "文本到视频",
    "image-to-video": "图像到视频",
    "automatic-speech-recognition": "语音识别",
    "text-to-speech": "语音合成",
    "multimodal": "多模态",
    "reinforcement-learning": "强化学习",
    "robotics": "机器人",
}

# 最低门槛（Phase 4 强化）
MIN_LIKES = 10
MIN_DOWNLOADS = 500


class HuggingFaceCollector(BaseCollector):
    """
    Hugging Face 采集器。

    通过 Hub API 获取近期热门模型，过滤核心 AI 类型。
    """

    def __init__(self, timeout: int = 30, token: str = "", max_models: int = 30):
        super().__init__(timeout)
        self.token = token or os.environ.get("HF_TOKEN", "")
        self.max_models = max_models

    @property
    def _headers(self) -> dict:
        h = {"User-Agent": "AIDailyNewsBot/1.0"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def fetch(self) -> list[dict]:
        """获取 HF 近期热门 AI 模型。"""
        candidates = []

        # 拉取最近更新的模型（按最后修改时间排序）
        models = self._fetch_models("lastModified")
        if not models:
            logger.warning("HF: no models returned from API, trying trending fallback...")
            # fallback: 尝试 trending endpoint（需要 HF_TOKEN）
            models = self._fetch_trending()

        logger.info("HF: fetched %d models from API", len(models))

        for model in models:
            candidate = self._model_to_candidate(model)
            if candidate:
                candidates.append(candidate)

        # 按社区热度排序
        candidates.sort(key=lambda c: c.get("_hf_hotness", 0), reverse=True)
        # 限制输出数量（避免 HF 条目泛滥）
        top_n = min(len(candidates), 5)
        logger.info(
            "HF: %d candidates → top %d (likes: %s)",
            len(candidates), top_n,
            ", ".join(str(c["metrics"].get("hf_likes", 0)) for c in candidates[:top_n]),
        )
        return candidates[:top_n]

    def _fetch_models(self, sort_by: str = "lastModified") -> list[dict]:
        """拉取模型列表。"""
        url = f"{HF_API_BASE}/models"
        params = {
            "sort": sort_by,
            "direction": "-1",
            "limit": self.max_models,
            "full": "true",
        }
        try:
            resp = requests.get(
                url, params=params, headers=self._headers, timeout=self.timeout,
            )
            if resp.status_code == 403:
                logger.warning("HF API rate limited (403), skipping")
                return []
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            logger.warning("HF API timeout")
            return []
        except Exception as e:
            logger.warning("HF API failed: %s", e)
            return []

    def _fetch_trending(self) -> list[dict]:
        """获取 HF trending 模型（备选方案）。"""
        url = f"{HF_API_BASE}/models"
        params = {
            "sort": "downloads",
            "direction": "-1",
            "limit": min(self.max_models, 15),
            "full": "true",
            "filter": "text-generation",
        }
        try:
            resp = requests.get(
                url, params=params, headers=self._headers, timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return []

    def _model_to_candidate(self, model: dict) -> Optional[dict]:
        """将 HF 模型转为统一 candidate。"""
        model_id = model.get("id", "")
        if not model_id:
            return None

        # 检查 pipeline tag
        pipeline_tag = model.get("pipeline_tag", "") or ""
        if pipeline_tag not in AI_PIPELINE_TAGS:
            return None

        # 质量门槛（Phase 4 强化）：
        # Primary: likes >= 10（社区认可）
        # Fallback: likes >= 3 AND downloads >= 5000（广泛使用但少点赞）
        # Below both: reject
        likes = model.get("likes", 0) or 0
        downloads = model.get("downloads", 0) or 0
        if likes < 10:
            if likes < 3 or downloads < 5000:
                return None
            # fallback: 标记为低 likes 边缘案例
            model["_hf_fallback"] = True

        # 检查是否有实质内容（model card / description）
        card_data = model.get("cardData", {}) or {}
        description = (
            card_data.get("language", []) or
            model.get("description", "") or ""
        )
        if isinstance(description, list):
            description = ", ".join(description)
        description = (description or "")[:300]

        title = model_id
        # 如果有更好的标题信息
        if card_data.get("model_name"):
            title = f"{card_data['model_name']} ({model_id})"
        elif model.get("modelId"):
            title = model.get("modelId", model_id)

        # 作者
        author = model.get("author", "") or model_id.split("/")[0] if "/" in model_id else ""

        # 时间：用 lastModified
        pub_time = None
        pub_source = "missing"
        last_mod = model.get("lastModified", "")
        if last_mod:
            try:
                pub_time = datetime.strptime(last_mod, "%Y-%m-%dT%H:%M:%S.%fZ")
                pub_time = pub_time.replace(tzinfo=timezone.utc)
                pub_source = "api"
            except ValueError:
                try:
                    pub_time = datetime.strptime(last_mod, "%Y-%m-%dT%H:%M:%SZ")
                    pub_time = pub_time.replace(tzinfo=timezone.utc)
                    pub_source = "api"
                except ValueError:
                    pass

        # 标签
        tags = model.get("tags", []) or []
        category = TAG_CATEGORY.get(pipeline_tag, pipeline_tag)
        tags.append(category)

        # 社区热度
        community_hotness = log1p(likes) * 3 + log1p(downloads) * 0.5

        candidate = self.make_candidate(
            id_=f"hf-{model_id}",
            title=title[:200],
            url=f"https://huggingface.co/{model_id}",
            source="Hugging Face",
            source_type="huggingface",
            published_at=pub_time,
            published_source=pub_source,
            summary=description,
            author=author,
            tags=tags,
            metrics={
                "hf_likes": likes,
                "hf_downloads": downloads,
            },
        )
        candidate["scores"]["community"] = round(community_hotness, 1)
        candidate["_hf_hotness"] = round(community_hotness, 1)
        candidate["_hf_category"] = category
        if model.get("_hf_fallback"):
            candidate["_hf_fallback"] = True
            candidate["_hf_fallback_reason"] = (
                f"likes={likes}<10, fallback via downloads={downloads}"
            )
        return candidate
