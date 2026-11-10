"""多厂商剧本模型：注册表、配置解析、三协议请求组装与响应归一化。"""

import os
import unittest
from dataclasses import replace
from unittest import mock

from videoforge import llm_providers
from videoforge.config import DeepSeekConfig, Settings
from videoforge.llm import DeepSeek, ModelNotFound


def make(provider: str, **kw) -> DeepSeek:
    prov = llm_providers.get(provider)
    cfg = DeepSeekConfig(provider=prov.id, protocol=prov.protocol, base_url=prov.base_url,
                         model=prov.models[0], api_key="sk-test")
    for k, v in kw.items():
        cfg = replace(cfg, **{k: v})
    return DeepSeek(cfg)


class TestRegistry(unittest.TestCase):
    def test_required_vendors_present(self):
        """用户点名要的六家必须在册。"""
        ids = set(llm_providers.ids())
        for pid in ("anthropic", "openai", "gemini", "xai", "zhipu", "moonshot"):
            self.assertIn(pid, ids)
        labels = " ".join(p.label for p in llm_providers.PROVIDERS)
        for brand in ("Claude", "ChatGPT", "Gemini", "Grok", "GLM", "Kimi"):
            self.assertIn(brand, labels)

    def test_protocols_are_known(self):
        for p in llm_providers.PROVIDERS:
            self.assertIn(p.protocol, ("openai", "anthropic", "gemini"), p.id)

    def test_describe_has_no_key_material(self):
        for item in llm_providers.describe_all():
            self.assertNotIn("api_key", item)

    def test_local_provider_is_keyless(self):
        ollama = llm_providers.get("ollama")
        self.assertTrue(ollama.keyless and ollama.local)

    def test_proxy_follows_cloud_but_not_local(self):
        """云端要走系统代理，本地 Ollama 不能被塞进代理。"""
        self.assertTrue(make("openai", api_key="sk-x").session.trust_env)
        self.assertFalse(make("ollama", api_key="").session.trust_env)


class TestSettingsResolution(unittest.TestCase):
    def test_env_picks_provider_and_model(self):
        env = {"LLM_PROVIDER": "anthropic", "LLM_MODEL": "claude-sonnet-4-5",
               "LLM_API_KEY": "sk-ant-xxx"}
        with mock.patch.dict(os.environ, env, clear=False):
            s = Settings._from_env()
        self.assertEqual(s.deepseek.provider, "anthropic")
        self.assertEqual(s.deepseek.protocol, "anthropic")
        self.assertEqual(s.deepseek.model, "claude-sonnet-4-5")
        self.assertTrue(s.deepseek.enabled)
        self.assertIn("Claude", s.deepseek.label)

    def test_provider_default_base_url(self):
        env = {"LLM_PROVIDER": "gemini", "LLM_API_KEY": "k"}
        os.environ.pop("LLM_BASE_URL", None)
        os.environ.pop("LLM_MODEL", None)
        with mock.patch.dict(os.environ, env, clear=False):
            s = Settings._from_env()
        self.assertEqual(s.deepseek.base_url, "https://generativelanguage.googleapis.com/v1beta")
        self.assertEqual(s.deepseek.model, "gemini-2.5-pro")

    def test_key_from_provider_slot(self):
        """切回某厂商时，从它自己的 Key 槽位取。"""
        env = {"LLM_PROVIDER": "moonshot", "LLM_KEY_MOONSHOT": "sk-moon"}
        os.environ.pop("LLM_API_KEY", None)
        with mock.patch.dict(os.environ, env, clear=False):
            s = Settings._from_env()
        self.assertEqual(s.deepseek.api_key, "sk-moon")

    def test_legacy_deepseek_env_still_works(self):
        for k in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
            os.environ.pop(k, None)
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-legacy"}, clear=False):
            s = Settings._from_env()
        self.assertEqual(s.deepseek.provider, "deepseek")
        self.assertEqual(s.deepseek.api_key, "sk-legacy")


class TestPayloads(unittest.TestCase):
    def test_openai_payload(self):
        c = make("openai")
        p = c._payload([{"role": "system", "content": "sys"},
                        {"role": "user", "content": "hi"}],
                       tools=None, json_mode=True, temperature=0.3, max_tokens=99, model=None)
        self.assertEqual(p["model"], "gpt-5.1")
        self.assertEqual(p["response_format"], {"type": "json_object"})
        self.assertEqual(p["max_tokens"], 99)
        self.assertNotIn("thinking", p)

    def test_anthropic_payload_moves_system_out(self):
        c = make("anthropic")
        p = c._payload([{"role": "system", "content": "you are a director"},
                        {"role": "user", "content": "写分镜"},
                        {"role": "assistant", "content": "好"},
                        {"role": "user", "content": "继续"}],
                       tools=None, json_mode=False, temperature=1.4, max_tokens=None, model=None)
        self.assertEqual(p["system"], "you are a director")
        self.assertEqual([m["role"] for m in p["messages"]], ["user", "assistant", "user"])
        self.assertLessEqual(p["temperature"], 1.0)          # Claude 上限 1.0
        self.assertNotIn("messages_system", p)

    def test_anthropic_endpoint(self):
        c = make("anthropic")
        self.assertEqual(c._endpoint("claude-sonnet-4-5"), "https://api.anthropic.com/v1/messages")
        self.assertEqual(c._headers()["anthropic-version"], "2023-06-01")
        self.assertNotIn("Authorization", c._headers())

    def test_gemini_payload_and_endpoint(self):
        c = make("gemini", model="gemini-2.5-flash")
        p = c._payload([{"role": "system", "content": "sys"},
                        {"role": "user", "content": "hi"}],
                       tools=None, json_mode=True, temperature=0.2, max_tokens=64, model=None)
        self.assertEqual(p["systemInstruction"]["parts"][0]["text"], "sys")
        self.assertEqual(p["contents"][0]["parts"][0]["text"], "hi")
        self.assertEqual(p["generationConfig"]["responseMimeType"], "application/json")
        self.assertTrue(c._endpoint("gemini-2.5-flash").endswith(
            "/models/gemini-2.5-flash:generateContent"))

    def test_anthropic_json_hint_injected(self):
        c = make("anthropic")
        p = c._payload([{"role": "user", "content": "x"}], tools=None,
                       json_mode=True, temperature=None, max_tokens=None, model=None)
        self.assertIn("JSON", p["system"])


class TestNormalization(unittest.TestCase):
    def test_anthropic_message(self):
        c = make("anthropic")
        msg = c._message({"content": [{"type": "text", "text": "第一段"},
                                      {"type": "tool_use", "id": "x"},
                                      {"type": "text", "text": "第二段"}]})
        self.assertEqual(msg["content"], "第一段第二段")

    def test_gemini_message(self):
        c = make("gemini")
        msg = c._message({"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
        self.assertEqual(msg["content"], "hi")

    def test_gemini_blocked(self):
        c = make("gemini")
        with self.assertRaises(Exception) as ctx:
            c._message({"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})
        self.assertIn("SAFETY", str(ctx.exception))

    def test_usage_tracking_for_each_protocol(self):
        c = make("anthropic")
        c._track_usage({"usage": {"input_tokens": 10, "output_tokens": 5}})
        self.assertEqual(c.usage["total_tokens"], 15)
        g = make("gemini")
        g._track_usage({"usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3}})
        self.assertEqual(g.usage["total_tokens"], 10)

    def test_model_error_detection(self):
        self.assertTrue(DeepSeek._looks_like_model_error(404, '{"error":"model not found"}'))
        self.assertTrue(DeepSeek._looks_like_model_error(400, '{"message":"Model Not Exist"}'))
        self.assertFalse(DeepSeek._looks_like_model_error(429, "rate limited"))

    def test_empty_content_with_reasoning_gives_clear_error(self):
        """思考型模型把预算花在 reasoning 上时，报错要说人话。"""
        c = make("ollama", api_key="")
        data = {"choices": [{"message": {"role": "assistant", "content": "",
                                         "reasoning": "先想一下……"},
                             "finish_reason": "length"}]}
        with self.assertRaises(Exception) as ctx:
            c._message(data)
        self.assertIn("思考", str(ctx.exception))

    def test_normal_content_unaffected(self):
        c = make("openai")
        msg = c._message({"choices": [{"message": {"role": "assistant", "content": "正常"}}]})
        self.assertEqual(msg["content"], "正常")


class TestAvailability(unittest.TestCase):
    def test_no_key_cloud_unavailable(self):
        c = make("openai", api_key="")
        self.assertFalse(c.available)

    def test_keyless_local_available(self):
        c = make("ollama", api_key="", model="qwen3:14b")
        self.assertTrue(c.available)

    def test_model_fallback_chain(self):
        c = make("deepseek", fallback_models=("deepseek-chat",))
        calls = []

        def fake_post(payload):
            calls.append(payload["model"])
            if payload["model"] == "deepseek-v4-flash":
                raise ModelNotFound("nope")
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        c._post = fake_post
        msg = c.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(msg["content"], "ok")
        self.assertEqual(calls, ["deepseek-v4-flash", "deepseek-chat"])


if __name__ == "__main__":
    unittest.main()
