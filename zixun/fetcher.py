"""HTTP 抓取：真实 UA、gzip 自动解压、礼貌限速、指数退避重试。

单线程顺序抓取（不并发），降低被反爬风控的概率。
"""
from __future__ import annotations

import logging
import random
import time

import requests

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class FetchError(Exception):
    """抓取失败（重试耗尽）。"""


class Fetcher:
    def __init__(
        self,
        ua: str = DEFAULT_UA,
        delay_range: tuple[float, float] = (1.0, 2.0),
        timeout: int = 20,
        max_retries: int = 3,
    ):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Connection": "keep-alive",
            }
        )
        self.delay_range = delay_range
        self.timeout = timeout
        self.max_retries = max_retries

    def _get(self, url: str) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                # mysteel 全站 UTF-8；显式指定避免 chardet 误判
                resp.encoding = "utf-8"
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                wait = min(2 ** attempt, 10) + random.uniform(0, 1)
                logger.warning(
                    "抓取失败 [%d/%d] %s: %s，%.1fs 后重试",
                    attempt, self.max_retries, url, exc, wait,
                )
                time.sleep(wait)
        raise FetchError(f"重试 {self.max_retries} 次仍失败: {url}") from last_exc

    def get_html(self, url: str) -> str:
        """抓取 URL 并返回 HTML 文本。每次请求后礼貌等待。"""
        logger.debug("GET %s", url)
        resp = self._get(url)
        html = resp.text
        time.sleep(random.uniform(*self.delay_range))
        return html

    def close(self):
        self.session.close()
