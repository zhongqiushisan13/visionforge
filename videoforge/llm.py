"""编剧大脑客户端：一套接口，三种协议。

- openai     OpenAI Chat Completions（DeepSeek / OpenAI / Grok / GLM / Kimi / 千问 /
             OpenRouter / Ollama / 自建网关）
- anthropic  Claude Messages（system 独立字段、content 为 block 数组）
- gemini     Google generateContent（contents + parts、systemInstruction）

对外只暴露 chat / text / json，上层完全不感知厂商差异。
特性保留：JSON 模式、模型回退、指数退避重试、用量统计、JSON 抢救解析。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

import requests

from .config import DeepSeekConfig
from .utils import log, warn

JSON_HINT = ("只输出一个 JSON 对象，不要任何解释、不要 markdown 代码围栏，字段名严格按约定。")


class LLMError(RuntimeError):
    pass


class ModelNotFound(LLMError):
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
                        snippet = re.sub(r",\s*([}\]])", r"\1", cand[start:i + 1])
                        try:
                            return json.loads(snippet)
                        except json.JSONDecodeError:
                            break
    raise LLMError(f"无法从模型输出中解析 JSON：{text[:220]}...")


def _system_text(messages: List[Dict[str, Any]]) -> str:
    return "\n\n".join(str(m.get("content") or "") for m in messages
                       if m.get("role") == "system").strip()


def _as_text(content: Any) -> str:
    """OpenAI 的 content 可能是字符串，也可能是 block 数组。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in ("text", "input_text"):
                    parts.append(str(block.get("text") or ""))
                elif "text" in block:
                    parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return "" if content is None else str(content)


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class DeepSeek:
    """历史类名保留（大量老代码与测试在用），实际按 cfg.protocol 走不同协议。"""

    def __init__(self, cfg: DeepSeekConfig):
        self.cfg = cfg
        self.session = requests.Session()
        # 云端厂商（OpenAI / Claude / Gemini / Grok）在国内通常要走代理，跟随 HTTP(S)_PROXY；
        # 但本地 Ollama / vLLM 不能往代理里塞，否则 127.0.0.1 的请求会被代理拒掉。
        self.session.trust_env = not cfg.is_local
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        self._model = cfg.model

    # ---------------- 底层 ---------------- #
    @property
    def available(self) -> bool:
        return bool(self.cfg.enabled)

    @property
    def label(self) -> str:
        return self.cfg.label

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.cfg.api_key
        if self.cfg.protocol == "anthropic":
            if key:
                headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
        elif self.cfg.protocol == "gemini":
            if key:
                headers["x-goog-api-key"] = key
        elif key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _endpoint(self, model: str) -> str:
        base = self.cfg.base_url.rstrip("/")
        if self.cfg.protocol == "anthropic":
            return base + "/v1/messages" if not base.endswith("/v1") else base + "/messages"
        if self.cfg.protocol == "gemini":
            if ":generateContent" in base:
                return base
            if model not in base:
                return f"{base}/models/{model}:generateContent"
            return f"{base}:generateContent"
        return base + "/chat/completions"

    def _payload(self, messages: List[Dict[str, Any]], *, tools: Optional[List[dict]],
                 json_mode: bool, temperature: Optional[float], max_tokens: Optional[int],
                 model: Optional[str]) -> Dict[str, Any]:
        model = model or self._model
        temp = self.cfg.temperature if temperature is None else temperature
        max_tok = max_tokens or self.cfg.max_tokens

        if self.cfg.protocol == "anthropic":
            system = _system_text(messages)
            if json_mode:
                system = (system + "\n\n" + JSON_HINT).strip()
            payload: Dict[str, Any] = {
                "model": model,
                "max_tokens": max_tok,
                "temperature": min(1.0, max(0.0, float(temp))),
                "messages": [{"role": "assistant" if m.get("role") == "assistant" else "user",
                              "content": _as_text(m.get("content"))}
                             for m in messages if m.get("role") in ("user", "assistant")],
            }
            if system:
                payload["system"] = system
            if tools:
                payload["tools"] = [self._anthropic_tool(t) for t in tools]
            return payload

        if self.cfg.protocol == "gemini":
            system = _system_text(messages)
            gen: Dict[str, Any] = {
                "temperature": max(0.0, float(temp)),
                "maxOutputTokens": max_tok,
            }
            if json_mode:
                gen["responseMimeType"] = "application/json"
            payload = {
                "contents": [{"role": "model" if m.get("role") == "assistant" else "user",
                              "parts": [{"text": _as_text(m.get("content"))}]}
                             for m in messages if m.get("role") in ("user", "assistant")],
                "generationConfig": gen,
            }
            if system:
                payload["systemInstruction"] = {"parts": [{"text": system}]}
            if tools:
                payload["tools"] = [{"functionDeclarations": [
                    {"name": t.get("function", {}).get("name", "tool"),
                     "description": t.get("function", {}).get("description", ""),
                     "parameters": t.get("function", {}).get("parameters", {"type": "object"})}
                    for t in tools]}]
            return payload

        # OpenAI 兼容
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # thinking / reasoning_effort 是 DeepSeek 系特有字段，别发给别家
        if self.cfg.provider == "deepseek":
            if self.cfg.thinking:
                payload["thinking"] = {"type": "enabled"}
            if self.cfg.reasoning_effort:
                payload["reasoning_effort"] = self.cfg.reasoning_effort
        return payload

    @staticmethod
    def _anthropic_tool(tool: dict) -> dict:
        fn = (tool or {}).get("function") or {}
        return {"name": fn.get("name", "tool"), "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}})}

    # ---------------- 请求 ---------------- #
    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        model = str(payload.get("model") or self._model)
        url = self._endpoint(model)
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
                if r.status_code in (401, 403):
                    raise LLMError(
                        f"{self.label} 鉴权失败（HTTP {r.status_code}）：检查 {self._key_hint()}，"
                        f"以及该 Key 是否有 {model} 的权限")
                if self._looks_like_model_error(r.status_code, body):
                    raise ModelNotFound(body)
                raise LLMError(f"{self.label} HTTP {r.status_code}: {body}")
            except ModelNotFound:
                raise
            except (requests.RequestException, LLMError) as exc:
                last = exc
                if attempt < self.cfg.retries:
                    wait = min(30, 2 ** attempt)
                    warn(f"{self.label} 调用失败({attempt}/{self.cfg.retries})，{wait}s 后重试：{exc}")
                    time.sleep(wait)
        raise LLMError(f"{self.label} 调用失败：{last}")

    def _key_hint(self) -> str:
        from . import llm_providers

        return f"环境变量 {llm_providers.get(self.cfg.provider).key_env}"

    @staticmethod
    def _looks_like_model_error(status: int, body: str) -> bool:
        low = body.lower()
        if status == 404 and ("model" in low or "not found" in low):
            return True
        return any(s in low for s in ("model_not_found", "model not exist",
                                      "does not exist", "is not found",
                                      "no such model", "invalid model"))

    def _track_usage(self, data: Dict[str, Any]) -> None:
        u = data.get("usage") or data.get("usageMetadata") or {}
        self.usage["calls"] += 1
        pairs = {
            "prompt_tokens": u.get("prompt_tokens") or u.get("input_tokens")
            or u.get("promptTokenCount") or 0,
            "completion_tokens": u.get("completion_tokens") or u.get("output_tokens")
            or u.get("candidatesTokenCount") or 0,
        }
        pairs["total_tokens"] = u.get("total_tokens") or u.get("totalTokenCount") \
            or (pairs["prompt_tokens"] + pairs["completion_tokens"])
        for k, v in pairs.items():
            self.usage[k] += int(v or 0)

    # ---------------- 响应归一化 ---------------- #
    def _message(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if self.cfg.protocol == "anthropic":
            blocks = data.get("content") or []
            text = "".join(str(b.get("text") or "") for b in blocks
                           if isinstance(b, dict) and b.get("type") == "text")
            if not text and data.get("stop_reason") == "refusal":
                raise LLMError(f"{self.label} 拒绝了本次请求（refusal）")
            return {"role": "assistant", "content": text}

        if self.cfg.protocol == "gemini":
            cands = data.get("candidates") or []
            if not cands:
                feedback = data.get("promptFeedback") or {}
                reason = feedback.get("blockReason") or "无候选结果"
                raise LLMError(f"{self.label} 没有返回内容（{reason}）")
            first = cands[0] or {}
            parts = ((first.get("content") or {}).get("parts")) or []
            text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
            if not text:
                reason = first.get("finishReason") or "空响应"
                raise LLMError(f"{self.label} 返回空内容（{reason}）")
            return {"role": "assistant", "content": text}

        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"{self.label} 响应结构异常：{str(data)[:300]}") from exc

    def _with_fallback(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self._post(payload)
        except ModelNotFound as exc:
            for alt in self.cfg.fallback_models or ():
                warn(f"模型 {payload.get('model')} 不可用，回退到 {alt}")
                payload = {**payload, "model": alt}
                try:
                    return self._post(payload)
                except ModelNotFound:
                    continue
            raise LLMError(
                f"模型 {payload.get('model')} 不存在或无权访问，检查模型名与账号权限。原始信息：{exc}"
            ) from exc

    # ---------------- 对外 ---------------- #
    def chat(self, messages: List[Dict[str, Any]], *, tools: Optional[List[dict]] = None,
             json_mode: bool = False, temperature: Optional[float] = None,
             max_tokens: Optional[int] = None, model: Optional[str] = None) -> Dict[str, Any]:
        """返回归一化后的 message（role + content）。"""
        if not self.available:
            hint = "填 API Key" if not self.cfg.is_local else "设 LLM_MODE=local"
            raise LLMError(
                f"剧本模型未配置（{self.label}）：{hint}；也可以把 Base URL 指向本地 "
                f"OpenAI 兼容服务（Ollama / vLLM / LM Studio），或用 mock 跑离线流程")
        payload = self._payload(messages, tools=tools, json_mode=json_mode,
                                temperature=temperature, max_tokens=max_tokens, model=model)
        data = self._with_fallback(payload)
        return self._message(data)

    def text(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        msg = self.chat(messages, **kwargs)
        content = msg.get("content") or ""
        if not content.strip():
            raise LLMError(f"{self.label} 返回空文本")
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
                    {"role": "user", "content": "上面的输出不是合法 JSON。请重新输出：" + JSON_HINT},
                ]
        raise LLMError(f"模型多次未产出合法 JSON：{last_exc}")

    def status(self) -> str:
        if not self.available:
            return "未配置 API Key"
        return (f"{self.label} · {self._model} | 调用 {self.usage['calls']} 次 | "
                f"tokens {self.usage['total_tokens']}")


# 新名字，供后续代码使用
LLMClient = DeepSeek
