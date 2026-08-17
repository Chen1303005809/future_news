"""LLM 客户端：OpenAI 协议封装。

- 强制 JSON 输出：``response_format={"type": "json_object"}``。
- 网络/超时/5xx/429 指数退避重试（``max_retries`` 次）。
- 返回 ``LLMResponse``（含原始文本、安全解析结果、token 用量、尝试次数），
  解析失败时 ``parsed`` 为 None，不抛异常，交由上层降级。
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import openai

logger = logging.getLogger("calibration.llm")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMConfigError(Exception):
    """OPENAI_API_KEY 未设置或无效。"""


@dataclass(frozen=True)
class LLMResponse:
    """一次成功的 LLM 调用结果。"""

    raw_text: str
    parsed: dict[str, Any] | None   # JSON 解析失败为 None
    model: str
    prompt_tokens: int
    completion_tokens: int
    attempt: int                     # 第几次调用成功（1-based）


def _make_client(base_url: str, timeout: float):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise LLMConfigError(
            "未设置环境变量 OPENAI_API_KEY（OpenAI 协议服务的 API key）"
        )
    return openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)


def _parse_json_safely(text: str) -> dict[str, Any] | None:
    """宽容解析 LLM 返回的 JSON。

    - 若为纯 JSON 直接解析；
    - 若被 ```json ... ``` 代码块包裹，剥壳后解析；
    - 提取首个 ``{...}`` 平衡块兜底。
    """
    if not text:
        return None
    s = text.strip()

    candidates: list[str] = []
    # 1) 原样
    candidates.append(s)
    # 2) 剥掉 ```json ... ``` 或 ```...```
    stripped = s
    if s.startswith("```"):
        stripped = s.split("```", 2)[1] if "```" in s[3:] else s[3:]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
        candidates.append(stripped)

    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue

    # 3) 提取首个 { ... } 平衡块
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(s[start : i + 1])
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    pass
                start = -1
    return None


class _EmptyChoicesError(Exception):
    """LLM 返回了空 choices。"""


def _attempt_llm(
    client,
    *,
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
) -> tuple[str, int, int]:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    choice = resp.choices[0] if resp.choices else None
    if choice is None:
        raise _EmptyChoicesError("LLM 返回空 choices")
    text = (choice.message.content or "").strip()
    prompt_tokens = getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0
    completion_tokens = getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0
    return text, int(prompt_tokens), int(completion_tokens)


# 可重试的 openai 异常类型（v3 未统一暴露 status_code，用类型判定）
_RETRYABLE = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


def _is_retryable(err: Exception) -> bool:
    if isinstance(err, _RETRYABLE):
        return True
    # 兜底：429/5xx 的 status 类错误
    status = getattr(err, "status_code", None) or getattr(
        getattr(err, "response", None), "status_code", None
    )
    return status in RETRYABLE_STATUS


def call_llm(
    messages: list[dict[str, str]],
    *,
    base_url: str,
    api_key: str | None = None,
    model: str,
    temperature: float,
    max_retries: int,
    timeout: float,
) -> LLMResponse:
    """调用 LLM，带重试。

    api_key 参数为 None 时从环境变量 OPENAI_API_KEY 读取（推荐，避免进 shell history）。
    """
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    client = _make_client(base_url, timeout)

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 2):  # 首次 + max_retries 次重试
        try:
            text, pt, ct = _attempt_llm(
                client, messages=messages, model=model, temperature=temperature
            )
            logger.debug("LLM 成功（第 %d 次尝试），%d 输入 / %d 输出 token", attempt, pt, ct)
            return LLMResponse(
                raw_text=text,
                parsed=_parse_json_safely(text),
                model=model,
                prompt_tokens=pt,
                completion_tokens=ct,
                attempt=attempt,
            )
        except LLMConfigError:
            raise  # key 缺失，重试无意义
        except openai.APIError as e:
            last_err = e
            if attempt > max_retries or not _is_retryable(e):
                break
            wait = 2 ** (attempt - 1)
            logger.warning(
                "LLM 调用失败（第 %d 次）：%s，%ss 后重试", attempt, e, wait
            )
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001 连接错误等
            last_err = e
            if attempt > max_retries:
                break
            wait = 2 ** (attempt - 1)
            logger.warning("LLM 调用异常（第 %d 次）：%s，%ss 后重试", attempt, e, wait)
            time.sleep(wait)

    raise last_err if last_err else LLMConfigError("LLM 调用失败（未知原因）")
