"""DeepSeek 客户端：OpenAI 兼容协议，负责剧本创作、分镜拆解、Agent 决策。

特性：JSON 模式、Function Calling、模型回退、指数退避重试、用量统计、
以及专治大模型「爱在 JSON 前后唠嗑」的解析器。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import DeepSeekConfig
from .utils import err, log, retry, warn


class LLMError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# JSON 抢救
# --------------------------------------------------------------------------- #
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """从模型输出里挖出第一段合法 JSON。容忍 ``` 围栏、前后废话、尾逗号。"""
    if not text:
        raise LLMError("模型返回空内容")
    text = text.strip()
    candidates: List[str] = []

    m = _FENCE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    candidates.append(text)

    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
        # 扫描平衡括号
        for opener, closer in (("{", "}"), ("[", "]")):
            start = cand.find(opener)
            if start < 0:
                continue
            depth, in_str, esc = 0, False, False
            for i in range(start, len(cand)):
                ch = cand[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        snippet = cand[start:i + 1]
                        snippet = re.sub(r",\s*([}\]])", r"\1", snippet)  # 去尾逗号
                        try:
                            return json.loads(snippet)
                        except json.JSONDecodeError:
                            break
    raise LLMError(f"无法从模型输出中解析 JSON：{text[:220]}...")


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class DeepSeek:
    def __init__(self, cfg: DeepSeekConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        self._model = cfg.model

    # ---------------- 底层 ---------------- #
    @property
    def available(self) -> bool:
        return bool(self.cfg.api_key)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        return headers

    def _payload(self, messages: List[Dict[str, Any]], *, tools: Optional[List[dict]],
                 json_mode: bool, temperature: Optional[float], max_tokens: Optional[int],
                 model: Optional[str]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model or self._model,
            "messages": messages,
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.cfg.max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.cfg.thinking:
            payload["thinking"] = {"type": "enabled"}
        if self.cfg.reasoning_effort:
            payload["reasoning_effort"] = self.cfg.reasoning_effort
        return payload

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        last: Optional[Exception] = None
        for attempt in range(1, self.cfg.retries + 1):
            try:
                r = self.session.post(url, headers=self._headers(), json=payload,
                                      timeout=self.cfg.timeout)
                if r.status_code == 200:
                    data = r.json()
                    self._track_usage(data)
                    return data
                body = r.text[:500]
                if r.status_code in (429, 500, 502, 503, 504):
                    raise LLMError(f"HTTP {r.status_code} 限流/服务异常: {body}")
                if r.status_code == 401:
                    raise LLMError("DeepSeek 鉴权失败：检查 DEEPSEEK_API_KEY")
                if "model_not_found" in body or "Model Not Exist" in body:
                    raise ModelNotFound(body)
                raise LLMError(f"HTTP {r.status_code}: {body}")
            except ModelNotFound:
                raise
            except (requests.RequestException, LLMError) as exc:
                last = exc
                if attempt < self.cfg.retries:
                    wait = min(30, 2 ** attempt)
                    warn(f"DeepSeek 调用失败({attempt}/{self.cfg.retries})，{wait}s 后重试：{exc}")
                    time.sleep(wait)
        raise LLMError(f"DeepSeek 调用失败：{last}")

    def _track_usage(self, data: Dict[str, Any]) -> None:
        u = data.get("usage") or {}
        self.usage["calls"] += 1
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.usage[k] += int(u.get(k) or 0)

    def _with_fallback(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self._post(payload)
        except ModelNotFound:
            for alt in self.cfg.fallback_models:
                warn(f"模型 {payload['model']} 不可用，回退到 {alt}")
                payload = {**payload, "model": alt}
                try:
                    return self._post(payload)
                except ModelNotFound:
                    continue
            raise LLMError("所有 DeepSeek 模型都不可用，检查账号权限或 DEEPSEEK_MODEL 配置")

    # ---------------- 对外 ---------------- #
    def chat(self, messages: List[Dict[str, Any]], *, tools: Optional[List[dict]] = None,
             json_mode: bool = False, temperature: Optional[float] = None,
             max_tokens: Optional[int] = None, model: Optional[str] = None) -> Dict[str, Any]:
        """返回原始 message 对象（可能含 content 或 tool_calls）。"""
        if not self.available:
            raise LLMError("剧本模型未配置：填 DEEPSEEK_API_KEY，或把 DEEPSEEK_BASE_URL 指向本地 "
                           "OpenAI 兼容服务并设 DEEPSEEK_MODE=local（也可用 mock 跑离线流程）")
        payload = self._payload(messages, tools=tools, json_mode=json_mode,
                                temperature=temperature, max_tokens=max_tokens, model=model)
        data = self._with_fallback(payload)
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"DeepSeek 响应结构异常：{str(data)[:300]}") from exc

    def text(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        msg = self.chat(messages, **kwargs)
        content = msg.get("content") or ""
        if not content.strip():
            raise LLMError("DeepSeek 返回空文本")
        return content

    def json(self, messages: List[Dict[str, Any]], *, retries: int = 2,
             repair: bool = True, **kwargs: Any) -> Any:
        """JSON 模式 + 解析失败自动「把错误回锅给模型」重修。"""
        kwargs.setdefault("json_mode", True)
        last_exc: Optional[Exception] = None
        convo = list(messages)
        for attempt in range(1, retries + 1):
            content = self.text(convo, **kwargs)
            try:
                return extract_json(content)
            except LLMError as exc:
                last_exc = exc
                warn(f"JSON 解析失败({attempt}/{retries})，让模型重修一次")
                if not repair or attempt >= retries:
                    break
                convo = list(messages) + [
                    {"role": "assistant", "content": content[:4000]},
                    {"role": "user", "content":
                        "上面的输出不是合法 JSON（或结构不符）。请只输出一个 JSON 对象，"
                        "不要任何解释、不要 markdown 代码围栏，字段名严格按约定。"},
                ]
        raise LLMError(f"模型多次未产出合法 JSON：{last_exc}")

    def status(self) -> str:
        if not self.available:
            return "未配置 API Key"
        return (f"{self._model} | 调用 {self.usage['calls']} 次 | "
                f"tokens {self.usage['total_tokens']}")


class ModelNotFound(LLMError):
    pass
