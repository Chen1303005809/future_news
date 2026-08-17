"""列表页与文章详情页解析（通用）。

适配 mysteel 的三种列表 URL 模式与多种详情页前缀（/a/、/gck/a/、/jkk/a/、/datas/a/ 等）。

注意：mysteel 早报/数据类文章的主要内容是 **AI 摘要**（.ai-summary__text），
传统长正文往往很短甚至只有联系方式。因此 AI 摘要作为核心字段提取，
正文过短时用摘要兜底，保证下游分析总有可用文本。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

# 文章详情页 URL 识别：
#   https://gc.mysteel.com/a/{dateId}/{HASH}.html
#   https://tks.mysteel.com/gck/a/{dateId}/{HASH}.html
#   https://tks.mysteel.com/jkk/a/{dateId}/{HASH}.html
#   https://tks.mysteel.com/datas/a/{dateId}/{HASH}.html
ARTICLE_URL_RE = re.compile(
    r"https?://[a-z0-9]+\.mysteel\.com/(?:[a-z0-9]+/)?a/\d{6,}/[A-F0-9]{8,}\.html",
    re.IGNORECASE,
)

# 发布时间提取（YYYY-MM-DD HH:MM[:SS]）
PUBLISH_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)")

# 文本中需剔除的噪声行
NOISE_LINE_KEYWORDS = [
    "智能摘要", "内容由AI生成",
    "资讯监督", "资讯编辑", "资讯投诉",
    "免责声明", "扫码下载", "关注公众号",
    "微信公众号", "客服", "版权声明", "Mysteel手机版",
]

# 正文过短阈值（字符数），低于此值则用 AI 摘要兜底
BODY_MIN_LEN = 20


@dataclass
class ArticleData:
    url: str
    title: str
    publish_time: str | None  # "YYYY-MM-DD HH:MM:SS"
    body_text: str
    ai_summary: str | None
    source: str | None


def parse_list_page(html: str) -> list[tuple[str, str]]:
    """从列表页提取 (文章 URL, 标题) 对，去重保序。"""
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    items: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not ARTICLE_URL_RE.match(href):
            continue
        title = (a.get("title") or a.get_text(strip=True)).strip()
        if not title:
            continue
        if href in seen:
            continue
        seen.add(href)
        items.append((href, title))
    return items


def _clean_text(s: str) -> str:
    """去空白行 + 剔除噪声行。"""
    lines: list[str] = []
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(k in line for k in NOISE_LINE_KEYWORDS):
            continue
        lines.append(line)
    return "\n".join(lines)


def parse_article(html: str, url: str, fallback_title: str = "") -> ArticleData:
    """解析文章详情页。"""
    soup = BeautifulSoup(html, "lxml")

    # ---- 标题（多策略）----
    title = fallback_title
    og = soup.select_one('meta[property="og:title"]')
    if og and og.get("content"):
        title = og["content"].strip()
    if not title:
        h1 = soup.select_one("h1")
        if h1:
            title = h1.get_text(strip=True)
    if not title:
        t = soup.select_one("title")
        if t:
            title = re.sub(
                r"\s*[-—|]\s*我的钢铁网\s*$", "", t.get_text(strip=True)
            )
    title = title.strip()

    # ---- 发布时间 ----
    publish_time: str | None = None
    meta_pub = soup.select_one('meta[name="publish"]')
    if meta_pub and meta_pub.get("content"):
        m = PUBLISH_TIME_RE.search(meta_pub["content"])
        if m:
            publish_time = m.group(1)
    if not publish_time:
        pt = soup.select_one(".publish-time")
        if pt:
            m = PUBLISH_TIME_RE.search(pt.get_text(strip=True))
            if m:
                publish_time = m.group(1)

    # ---- AI 摘要（核心内容）----
    # 优先 .ai-summary__text（<pre>，纯文本最干净），回退 .ai-summary__body
    ai_summary: str | None = None
    sum_node = soup.select_one(".ai-summary__text") or soup.select_one(
        ".ai-summary__body"
    )
    if sum_node:
        ai_summary = _clean_text(sum_node.get_text("\n")) or None

    # ---- 正文 ----
    body_text = ""
    body_node = soup.select_one("#article-content") or soup.select_one(
        ".content-text"
    )
    if body_node:
        # 移除嵌入的 AI 摘要块（避免正文重复摘要），按 class 与 id 双保险
        for sel in (".ai-summary", "#articleAiSummary"):
            for node in body_node.select(sel):
                node.decompose()
        body_text = _clean_text(body_node.get_text("\n"))

    # 兜底：短讯/数据类文章正文往往只有联系方式，去噪后所剩无几，用摘要填充
    if len(body_text) < BODY_MIN_LEN and ai_summary:
        body_text = ai_summary

    # ---- 来源 ----
    source: str | None = None
    src_node = soup.select_one(".article-source")
    if src_node:
        source = src_node.get_text(strip=True)
        source = re.sub(r"^来源[：:]\s*", "", source).strip() or None

    return ArticleData(
        url=url,
        title=title,
        publish_time=publish_time,
        body_text=body_text,
        ai_summary=ai_summary,
        source=source,
    )
