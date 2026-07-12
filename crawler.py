from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Settings


LOGGER = logging.getLogger(__name__)
TRENDING_URL = "https://github.com/trending?since=daily"
API_ROOT = "https://api.github.com"
FOCUS_KEYWORDS = (
    "ai", "artificial intelligence", "llm", "large language model", "agent",
    "automation", "python", "rust", "data science", "machine learning",
    "deep learning", "open source", "developer tool", "workflow", "rag",
)


@dataclass
class Repository:
    rank: int
    full_name: str
    url: str
    stars: int
    stars_today: int
    language: str
    description: str
    readme: str
    topics: list[str]
    focus_score: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(text: str) -> int:
    match = re.search(r"[\d,]+", text or "")
    return int(match.group(0).replace(",", "")) if match else 0


def _clean_readme(raw: str, limit: int) -> str:
    text = re.sub(r"!\[[^]]*]\([^)]*\)", "", raw)
    text = re.sub(r"<img\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


class GitHubTrendingCrawler:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update(
            {
                "User-Agent": "github-trend-bot/1.0",
                "Accept-Language": "en-US,en;q=0.8",
            }
        )
        self.api_headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if cfg.github_token:
            self.api_headers["Authorization"] = f"Bearer {cfg.github_token}"

    def collect(self) -> list[Repository]:
        self.cfg.validate_collection()
        response = self.session.get(TRENDING_URL, timeout=self.cfg.request_timeout)
        response.raise_for_status()
        candidates = self._parse_trending(response.text)
        if not candidates:
            raise RuntimeError("GitHub Trending 页面未解析到项目，页面结构可能已变化")

        repositories: list[Repository] = []
        for candidate in candidates[: self.cfg.trending_limit]:
            repositories.append(self._enrich(candidate))
        LOGGER.info("采集完成：%d 个项目", len(repositories))
        return repositories

    def _parse_trending(self, html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, Any]] = []
        for rank, article in enumerate(soup.select("article.Box-row"), start=1):
            link = article.select_one("h2 a")
            if not link or not link.get("href"):
                continue
            full_name = str(link["href"]).strip("/").replace(" ", "")
            if full_name.count("/") != 1:
                continue
            description_node = article.select_one("p")
            language_node = article.select_one("[itemprop='programmingLanguage']")
            stars_node = article.select_one(f"a[href='/{full_name}/stargazers']")
            daily_node = article.select_one("span.d-inline-block.float-sm-right")
            daily_text = daily_node.get_text(" ", strip=True) if daily_node else article.get_text(" ", strip=True)
            today_match = re.search(r"([\d,]+)\s+stars?\s+today", daily_text, re.I)
            results.append(
                {
                    "rank": rank,
                    "full_name": full_name,
                    "url": f"https://github.com/{full_name}",
                    "stars": _number(stars_node.get_text(" ", strip=True) if stars_node else ""),
                    "stars_today": _number(today_match.group(1) if today_match else ""),
                    "language": language_node.get_text(strip=True) if language_node else "Unknown",
                    "description": description_node.get_text(" ", strip=True) if description_node else "",
                }
            )
        return results

    def _enrich(self, item: dict[str, Any]) -> Repository:
        full_name = item["full_name"]
        topics: list[str] = []
        try:
            response = self.session.get(
                f"{API_ROOT}/repos/{full_name}",
                headers=self.api_headers,
                timeout=self.cfg.request_timeout,
            )
            response.raise_for_status()
            metadata = response.json()
            item["stars"] = int(metadata.get("stargazers_count") or item["stars"])
            item["language"] = metadata.get("language") or item["language"]
            item["description"] = metadata.get("description") or item["description"]
            topics = [str(topic) for topic in metadata.get("topics", [])]
        except (requests.RequestException, ValueError) as exc:
            LOGGER.warning("获取 %s 元数据失败，使用 Trending 页面数据: %s", full_name, exc)

        readme = ""
        try:
            headers = dict(self.api_headers)
            headers["Accept"] = "application/vnd.github.raw+json"
            response = self.session.get(
                f"{API_ROOT}/repos/{full_name}/readme",
                headers=headers,
                timeout=self.cfg.request_timeout,
            )
            if response.status_code != 404:
                response.raise_for_status()
                readme = _clean_readme(response.text, self.cfg.readme_max_chars)
        except requests.RequestException as exc:
            LOGGER.warning("获取 %s README 失败: %s", full_name, exc)

        haystack = " ".join(
            [full_name, item["description"], item["language"], *topics]
        ).lower()
        focus_score = sum(1 for keyword in FOCUS_KEYWORDS if keyword in haystack)
        return Repository(
            **item,
            readme=readme,
            topics=topics,
            focus_score=focus_score,
        )

