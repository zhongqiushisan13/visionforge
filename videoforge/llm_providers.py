"""编剧大脑的厂商注册表：一个模型一个入口，协议只分三类。

- `openai`   OpenAI Chat Completions 协议（DeepSeek / OpenAI / Grok / GLM / Kimi /
             通义千问 / OpenRouter / Ollama 都走这套，改 base_url 即可）
- `anthropic` Claude Messages 协议（system 单独传、content 是 block 数组）
- `gemini`   Google Generative Language 协议（contents + parts、systemInstruction）

这里只描述「怎么连」，真正的请求组装在 llm.py。新增厂商 = 往 PROVIDERS 里加一条。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Provider:
    id: str
    label: str                     # 品牌名，各语言都用原文
    protocol: str                  # openai / anthropic / gemini
    base_url: str
    models: Tuple[str, ...]
    key_env: str                   # 该厂商密钥的环境变量名
    keyless: bool = False          # 本地服务可无 Key
    local: bool = False            # 是否本机地址（默认值，可被用户改）
    docs: str = ""
    note: str = ""                 # 一句话说明，给设置页用

    @property
    def chat_path(self) -> str:
        if self.protocol == "anthropic":
            return "/v1/messages"
        if self.protocol == "gemini":
            return ""              # Gemini 的路径带模型名，单独拼
        return "/chat/completions"

    def models_or(self, extra: List[str]) -> List[str]:
        seen, out = set(), []
        for m in list(self.models) + list(extra or []):
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out


PROVIDERS: Tuple[Provider, ...] = (
    Provider(
        id="deepseek", label="DeepSeek", protocol="openai",
        base_url="https://api.deepseek.com",
        models=("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"),
        key_env="DEEPSEEK_API_KEY", docs="https://platform.deepseek.com",
        note="中文剧本稳、价格低",
    ),
    Provider(
        id="openai", label="OpenAI · ChatGPT", protocol="openai",
        base_url="https://api.openai.com/v1",
        models=("gpt-5.1", "gpt-5.1-mini", "gpt-5", "gpt-4.1", "gpt-4o"),
        key_env="OPENAI_API_KEY", docs="https://platform.openai.com",
        note="通用能力最均衡",
    ),
    Provider(
        id="anthropic", label="Anthropic · Claude", protocol="anthropic",
        base_url="https://api.anthropic.com",
        models=("claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"),
        key_env="ANTHROPIC_API_KEY", docs="https://console.anthropic.com",
        note="长剧本与文学性台词更强",
    ),
    Provider(
        id="gemini", label="Google · Gemini", protocol="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        models=("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"),
        key_env="GEMINI_API_KEY", docs="https://aistudio.google.com",
        note="超长上下文，适合长片分镜",
    ),
    Provider(
        id="xai", label="xAI · Grok", protocol="openai",
        base_url="https://api.x.ai/v1",
        models=("grok-4", "grok-4-fast", "grok-3"),
        key_env="XAI_API_KEY", docs="https://console.x.ai",
        note="口语化、梗多，适合网感脚本",
    ),
    Provider(
        id="zhipu", label="Zhipu · GLM", protocol="openai",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=("glm-4.6", "glm-4.5", "glm-4.5-air"),
        key_env="ZHIPU_API_KEY", docs="https://open.bigmodel.cn",
        note="国内直连、中文表达自然",
    ),
    Provider(
        id="moonshot", label="Moonshot · Kimi", protocol="openai",
        base_url="https://api.moonshot.cn/v1",
        models=("kimi-k2-0905-preview", "kimi-k2-turbo-preview", "moonshot-v1-128k"),
        key_env="MOONSHOT_API_KEY", docs="https://platform.moonshot.cn",
        note="长文本理解、中文语感好",
    ),
    Provider(
        id="qwen", label="Alibaba · Qwen", protocol="openai",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=("qwen3-max", "qwen-plus", "qwen3-coder-plus"),
        key_env="DASHSCOPE_API_KEY", docs="https://dashscope.console.aliyun.com",
        note="国内直连、性价比高",
    ),
    Provider(
        id="openrouter", label="OpenRouter", protocol="openai",
        base_url="https://openrouter.ai/api/v1",
        models=("openai/gpt-5.1", "anthropic/claude-sonnet-4.5", "google/gemini-2.5-pro",
                "x-ai/grok-4", "deepseek/deepseek-v4"),
        key_env="OPENROUTER_API_KEY", docs="https://openrouter.ai/keys",
        note="一个 Key 调所有模型，按量计费",
    ),
    Provider(
        id="ollama", label="Ollama · Local", protocol="openai",
        base_url="http://127.0.0.1:11434/v1",
        models=("qwen3:14b", "qwen2.5:14b", "llama3.3:70b"),
        key_env="OLLAMA_API_KEY", keyless=True, local=True,
        docs="https://ollama.com", note="零成本、离线可用",
    ),
    Provider(
        id="custom", label="Custom · OpenAI-compatible", protocol="openai",
        base_url="http://127.0.0.1:8000/v1",
        models=(), key_env="LLM_API_KEY", keyless=True, local=True,
        note="vLLM / LM Studio / 自建网关",
    ),
)

BY_ID: Dict[str, Provider] = {p.id: p for p in PROVIDERS}
DEFAULT_PROVIDER = "deepseek"


def get(provider_id: str) -> Provider:
    return BY_ID.get((provider_id or "").strip().lower(), BY_ID[DEFAULT_PROVIDER])


def ids() -> List[str]:
    return [p.id for p in PROVIDERS]


def detect(key_env_values: Dict[str, str]) -> Optional[str]:
    """按「谁有 Key」猜一个默认厂商：先看用户填过的，再按注册表顺序。"""
    for p in PROVIDERS:
        if p.keyless:
            continue
        if (key_env_values.get(p.key_env) or "").strip():
            return p.id
    return None


def describe_all() -> List[dict]:
    """给设置页用的清单（不含任何密钥）。"""
    return [
        {
            "id": p.id, "label": p.label, "protocol": p.protocol, "base_url": p.base_url,
            "models": list(p.models), "key_env": p.key_env, "keyless": p.keyless,
            "local": p.local, "docs": p.docs, "note": p.note,
        }
        for p in PROVIDERS
    ]
