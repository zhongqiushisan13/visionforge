"""配音适配器：MiniMax speech T2A 为主，edge-tts 次之，最后静音兜底（保时间线不塌）。"""

from __future__ import annotations

import binascii
import re
from pathlib import Path
from typing import Optional

import requests

from ..config import MiniMaxConfig
from ..utils import ensure_dir, ffmpeg, log, probe_duration, warn

_CJK = re.compile(r"[\u4e00-\u9fff]")


def clean_text(text: str) -> str:
    text = re.sub(r"^\s*[【\[(（]?[^：:\n]{1,12}[】\])）]?\s*[：:]\s*", "", text or "")
    text = re.sub(r"[*_#`>]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def estimate_speech_seconds(text: str, speed: float = 1.0) -> float:
    """粗略估时长：中日韩 4.3 字/秒，拉丁 2.6 词/秒，再叠标点停顿。"""
    text = clean_text(text)
    if not text:
        return 0.0
    cjk = len(_CJK.findall(text))
    latin_words = len(re.findall(r"[A-Za-z']+", text))
    base = cjk / 4.3 + latin_words / 2.6
    pauses = 0.25 * len(re.findall(r"[。！？!?；;，,、]", text))
    return round(max(0.8, (base + pauses) / max(0.5, speed)), 2)


class MiniMaxTTS:
    name = "minimax-tts"

    def __init__(self, cfg: MiniMaxConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.chars_used = 0

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.api_key)

    def synthesize(self, text: str, out_path: Path, *, voice_id: str = "",
                   speed: Optional[float] = None) -> float:
        text = clean_text(text)
        if not text:
            return 0.0
        ensure_dir(out_path.parent)
        payload = {
            "model": self.cfg.tts_model,
            "text": text,
            "stream": False,
            "language_boost": "auto",
            "output_format": "hex",
            "voice_setting": {
                "voice_id": voice_id or self.cfg.voice_id,
                "speed": speed if speed is not None else self.cfg.tts_speed,
                "vol": self.cfg.tts_volume,
                "pitch": self.cfg.tts_pitch,
            },
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000,
                              "format": "mp3", "channel": 1},
        }
        try:
            r = self.session.post(self.cfg.base_url.rstrip("/") + "/v1/t2a_v2",
                                  headers={"Authorization": f"Bearer {self.cfg.api_key}",
                                           "Content-Type": "application/json"},
                                  json=payload, timeout=120)
        except requests.RequestException as exc:
            raise RuntimeError(f"TTS 网络异常：{exc}") from exc

        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        base = data.get("base_resp") or {}
        if base.get("status_code") not in (None, 0):
            raise RuntimeError(f"TTS 失败 {base.get('status_code')}: {base.get('status_msg')}")
        audio_hex = (data.get("data") or {}).get("audio")
        if not audio_hex:
            raise RuntimeError(f"TTS 返回无音频：{str(data)[:200]}")
        try:
            out_path.write_bytes(binascii.unhexlify(audio_hex))
        except binascii.Error as exc:
            raise RuntimeError(f"TTS 音频解码失败：{exc}") from exc

        self.chars_used += len(text)
        dur = probe_duration(out_path)
        info = data.get("extra_info") or {}
        if info.get("audio_length"):
            dur = max(dur, float(info["audio_length"]) / 1000.0)
        log(f"配音完成 {out_path.name} ({len(text)}字 / {dur:.1f}s)", tag="tts")
        return round(dur, 2)


class EdgeTTS:
    """免费备胎：装了 edge-tts 就用，没装自动降级。"""

    name = "edge-tts"

    def __init__(self, voice: str = "zh-CN-YunxiNeural"):
        self.voice = voice

    @property
    def enabled(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    def synthesize(self, text: str, out_path: Path, *, voice_id: str = "", speed=None) -> float:
        import asyncio

        import edge_tts

        text = clean_text(text)
        if not text:
            return 0.0
        rate = "+0%"
        if speed and speed != 1.0:
            rate = f"{int(round((speed - 1) * 100)):+d}%"
        ensure_dir(out_path.parent)
        asyncio.run(edge_tts.Communicate(text, voice_id or self.voice, rate=rate).save(str(out_path)))
        return round(probe_duration(out_path), 2)


class SilentTTS:
    """静音轨：保证每条镜头都有等长音轨，方便后续对位与混音。"""

    name = "silent"

    def synthesize(self, text: str, out_path: Path, *, voice_id: str = "",
                   speed: Optional[float] = None) -> float:
        dur = max(1.2, estimate_speech_seconds(text, speed or 1.0))
        ensure_dir(out_path.parent)
        ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=32000:cl=mono", "-t", f"{dur:.2f}",
                "-c:a", "libmp3lame", "-q:a", "7", str(out_path)], timeout=120)
        return round(dur, 2)


class VoiceoverEngine:
    """按配置挑一个能用的引擎；全都不可用就走静音，绝不因为配音中断整条片子。"""

    def __init__(self, cfg: MiniMaxConfig, *, prefer: str = "auto"):
        self.minimax = MiniMaxTTS(cfg)
        self.edge = EdgeTTS()
        self.silent = SilentTTS()
        self.prefer = prefer

    def _chain(self):
        if self.prefer == "silent":
            return [self.silent]
        if self.prefer == "edge":
            return [self.edge, self.silent]
        chain = []
        if self.minimax.enabled:
            chain.append(self.minimax)
        chain.append(self.edge)
        chain.append(self.silent)
        return chain

    def synthesize(self, text: str, out_path: Path, *, voice_id: str = "",
                   speed: Optional[float] = None) -> tuple[float, str]:
        last_err: Optional[Exception] = None
        for engine in self._chain():
            try:
                dur = engine.synthesize(text, out_path, voice_id=voice_id, speed=speed)
                if dur > 0.05:
                    return dur, engine.name
            except Exception as exc:  # 换下一个引擎，不炸整片
                last_err = exc
                warn(f"{engine.name} 配音失败，换下一个：{exc}")
        warn(f"全部配音引擎失败，静音兜底：{last_err}")
        return self.silent.synthesize(text, out_path), "silent"
