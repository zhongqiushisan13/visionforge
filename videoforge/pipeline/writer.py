"""编剧：DeepSeek 两段式产出「剧本 → 分镜表」。

为什么要分两段：一次让模型同时编故事和写英文提示词，它会把叙事精力全花在提示词上，
故事变成流水账。先立骨架（人物/节拍/风格），再按节拍拆镜头，成片逻辑才立得住。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..editing import FILTER_PRESETS, TITLE_POSITIONS, TRANSITIONS, clamp
from ..llm import DeepSeek, LLMError
from ..schemas import Character, Script, Shot
from ..utils import log, warn

SYSTEM = """你是「VisionForge」的首席编剧兼导演，专攻 30~180 秒的竖屏/横屏短片（品牌片、概念片、微叙事）。
你同时精通 AI 视频模型的脾气：镜头必须短（4~10 秒）、单一动作、单一运镜、主体清晰，
否则模型会糊、会变形、会一镜里突然换人。

硬性纪律：
1. 只输出 JSON，不要 markdown 围栏，不要解释。
2. 所有面向画面的提示词用英文写，叙事与台词用中文（除非用户指定英文）。
3. 不写模型做不到的事：一镜内不换场景、不切视角、不出现多人复杂互动、不要求精确文字。
4. 不写真实品牌、真人明星、政治宗教敏感内容。"""

OUTLINE_USER = """请为下面这支短片写创作大纲。

【需求】
{brief}

【硬约束】
- 成片时长目标：{duration} 秒
- 画幅：{aspect}
- 语言：{language}
- 影像风格倾向：{style}
- 镜头时长区间：4~10 秒（AI 视频模型的舒适区）

【输出 JSON 结构】
{{
  "title": "中文片名，8 字以内，有画面感",
  "logline": "一句话故事（30 字内）",
  "synopsis": "150 字内故事梗概，写清起承转合",
  "tone": "情绪基调，如：克制的温情 / 冷峻的悬疑",
  "genre": "类型，如：品牌概念片 / 微叙事 / 产品片",
  "characters": [
    {{"name": "角色名", "look": "中文外形与服装描述", "look_en": "consistent English appearance description, 15-30 words, reusable across shots", "voice": "音色建议"}}
  ],
  "beats": [
    {{"act": "第一幕 建立", "summary": "这一段落发生什么", "emotion": "情绪", "duration": 15}}
  ],
  "style_prompt": "one-line English visual style, e.g. 'cinematic still, 35mm anamorphic, moody teal-orange grade, volumetric rain light, shallow depth of field'",
  "music_mood": "配乐气质，如：低频钢琴 + 雨声环境"
}}

beats 的 duration 相加必须约等于 {duration}。人物不超过 3 个。"""

SHOTS_USER = """根据大纲拆解分镜表。这是要直接喂给 ComfyUI（关键帧）和 MiniMax H3（视频）的生产任务单，
写错一个字就是废片重跑，所以每个字段都要能落地。

【大纲】
{outline}

【硬约束】
- 总时长 ≈ {duration} 秒（误差 ±10%），每条镜头 duration 为 4~10 的整数
- 镜头数建议 {shot_count} 条左右
- video_prompt：英文，单镜头单动作，结构为「主体 + 动作 + 环境 + 运镜 + 光线 + 风格」，
  不超过 70 词；不得出现画面文字、字幕、logo、水印；人物出现时用其 look_en 的关键特征锚定身份
- image_prompt：英文静帧提示词，描述这一镜的开画瞬间（构图 + 景别 + 光线 + 风格），供出图用
- negative_prompt：英文，默认 "text, watermark, logo, distorted face, extra limbs, low quality"
- dialogue：{language} 旁白/台词，单条不超过 30 字，口语、有钩子，别写成说明书
- speaker：说话人；纯旁白写 "旁白"
- subtitle：字幕文本，通常等于 dialogue，过长时可精简
- camera：中文运镜，如「缓慢推近」「手持跟随」「横移摇摄」
- transition：cut 或 fade（fade 只用在段落交界）

【输出 JSON 结构】
{{"shots": [
  {{"id": "S01", "act": "第一幕 建立", "duration": 6,
    "scene": "雨夜的老街面馆，招牌灯管忽明忽暗",
    "description": "中文画面描述",
    "camera": "缓慢推近",
    "dialogue": "中文旁白",
    "speaker": "旁白",
    "subtitle": "字幕",
    "sfx": "雨声、远处车流",
    "image_prompt": "English still prompt",
    "video_prompt": "English motion prompt",
    "negative_prompt": "text, watermark, logo, distorted face, extra limbs, low quality",
    "transition": "cut"}}
]}}

第一条镜头必须是能立住调性的建立镜头，最后一条要收得住。"""


class Screenwriter:
    def __init__(self, settings: Settings, llm: Optional[DeepSeek] = None):
        self.s = settings
        self.llm = llm or DeepSeek(settings.deepseek)

    # ------------------------------------------------------------------ #
    def write(self, brief: str, *, title: str = "", duration: int = 60,
              aspect: str = "16:9", style: str = "", language: str = "zh",
              shot_hint: int = 0) -> tuple[Script, List[Shot]]:
        if not self.llm.available or self.s.pipeline.mock:
            warn("未配置 DeepSeek 或处于 mock 模式，使用内置示例分镜（仅用于链路验证）")
            return self._mock(title or "雨停之前", duration, style)

        outline = self._outline(brief, duration, aspect, style, language)
        script = self._to_script(outline, duration, language, style)
        shots = self._shots(script, duration, shot_hint, language)
        shots = self._normalize(shots, script, duration)
        log(f"剧本完成：《{script.title}》 {len(shots)} 条镜头 / {sum(s.duration for s in shots)}s")
        return script, shots

    # ------------------------------------------------------------------ #
    def _outline(self, brief: str, duration: int, aspect: str, style: str, language: str) -> Dict[str, Any]:
        user = OUTLINE_USER.format(brief=brief.strip(), duration=duration, aspect=aspect,
                                   style=style or "由你根据主题决定", language=language)
        data = self.llm.json([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ], temperature=1.25, max_tokens=3000)
        if not isinstance(data, dict) or not data.get("title"):
            raise LLMError(f"大纲结构不合法：{str(data)[:200]}")
        return data

    def _to_script(self, outline: Dict[str, Any], duration: int, language: str, style: str) -> Script:
        sc = Script.from_dict(outline)
        sc.target_duration = duration
        sc.language = language
        sc.raw = json.dumps(outline, ensure_ascii=False)
        if not sc.style_prompt:
            sc.style_prompt = style or "cinematic, natural lighting, shallow depth of field"
        if not sc.characters:
            sc.characters = []
        for c in sc.characters:
            if not c.look_en:
                c.look_en = c.look
        return sc

    def _shots(self, script: Script, duration: int, shot_hint: int, language: str) -> List[Shot]:
        shot_count = shot_hint or max(4, round(duration / 6.5))
        outline = {
            "title": script.title, "logline": script.logline, "synopsis": script.synopsis,
            "tone": script.tone, "genre": script.genre,
            "characters": [{"name": c.name, "look": c.look, "look_en": c.look_en} for c in script.characters],
            "beats": script.beats, "style_prompt": script.style_prompt,
            "music_mood": script.music_mood,
        }
        user = SHOTS_USER.format(outline=json.dumps(outline, ensure_ascii=False, indent=1),
                                 duration=duration, shot_count=shot_count, language=language)
        data = self.llm.json([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ], temperature=1.05, max_tokens=6000)
        raw_shots = data.get("shots") if isinstance(data, dict) else None
        if not raw_shots:
            raise LLMError("分镜表为空")
        shots = [Shot.from_dict(x) for x in raw_shots if isinstance(x, dict)]
        if not shots:
            raise LLMError("分镜表解析后为空")
        return shots

    # ------------------------------------------------------------------ #
    def _normalize(self, shots: List[Shot], script: Script, target: int) -> List[Shot]:
        """兜底修正：时长夹紧、id 补全、提示词补风格锚、字幕落位。"""
        style = (script.style_prompt or "").strip().rstrip(".")
        chars = " ".join(c.look_en for c in script.characters if c.look_en)

        for i, shot in enumerate(shots, 1):
            if not shot.id:
                shot.id = f"S{i:02d}"
            shot.index = i
            shot.duration = max(4, min(10, int(shot.duration or 6)))
            shot.transition = (shot.transition or "cut").lower()
            if shot.transition not in TRANSITIONS:
                shot.transition = "cut"
            shot.transition_duration = clamp(float(shot.transition_duration or 0), 0, 1)
            shot.speed = clamp(float(shot.speed or 1), 0.25, 4)
            shot.volume = clamp(float(shot.volume or 1), 0, 2)
            shot.brightness = clamp(float(shot.brightness or 0), -0.5, 0.5)
            shot.contrast = clamp(float(shot.contrast or 1), 0.5, 2)
            shot.saturation = clamp(float(shot.saturation or 1), 0, 2)
            shot.temperature = clamp(float(shot.temperature or 0), -1, 1)
            if shot.filter_preset not in FILTER_PRESETS:
                shot.filter_preset = "none"
            if shot.title_position not in TITLE_POSITIONS:
                shot.title_position = "bottom"

            shot.video_prompt = (shot.video_prompt or shot.image_prompt or shot.description).strip()
            shot.image_prompt = (shot.image_prompt or shot.video_prompt).strip()
            if style:
                if style.split(",")[0].lower() not in shot.video_prompt.lower():
                    shot.video_prompt = f"{shot.video_prompt.rstrip('.')}. {style}."
                if style.split(",")[0].lower() not in shot.image_prompt.lower():
                    shot.image_prompt = f"{shot.image_prompt.rstrip('.')}. {style}."
            # 身份锚定：有角色但提示词没提，就补一句，减少「每镜换人」
            if chars and not any(c.name and c.name in shot.description for c in script.characters):
                pass  # 名字是中文，英文提示词里不出现，交给编剧写 look_en
            if not shot.negative_prompt:
                shot.negative_prompt = self.s.comfy.negative
            shot.dialogue = (shot.dialogue or "").strip()
            shot.subtitle = (shot.subtitle or shot.dialogue).strip()
            if len(shot.subtitle) > 42:                     # 一行塞不下，交给字幕器折行
                shot.subtitle = shot.subtitle
            shot.updated_at = ""

        total = sum(s.duration for s in shots)
        if abs(total - target) / max(1, target) > 0.2:
            warn(f"分镜总时长 {total}s 与目标 {target}s 偏差较大，成片节奏可能不对")
            shots = self._rebalance(shots, target)
        return shots

    @staticmethod
    def _rebalance(shots: List[Shot], target: int) -> List[Shot]:
        """按比例把总时长拉回目标，仍限制在 4~10 秒。"""
        total = sum(s.duration for s in shots)
        if total == 0:
            return shots
        scale = target / total
        for s in shots:
            s.duration = max(4, min(10, int(round(s.duration * scale))))
        diff = target - sum(s.duration for s in shots)
        i = 0
        while diff != 0 and i < len(shots) * 4:
            s = shots[i % len(shots)]
            step = 1 if diff > 0 else -1
            if 4 <= s.duration + step <= 10:
                s.duration += step
                diff -= step
            i += 1
        return shots

    # ------------------------------------------------------------------ #
    def _mock(self, title: str, duration: int, style: str) -> tuple[Script, List[Shot]]:
        """离线示例：8 条镜头、约 56 秒，用来验证整条流水线。"""
        style_prompt = style or ("cinematic still, 35mm anamorphic, moody teal-orange grade, "
                                 "volumetric light, shallow depth of field, film grain")
        beats = [
            {"act": "第一幕 建立", "summary": "雨夜的老街，面馆是唯一亮着的窗", "emotion": "疲惫中的暖意", "duration": 18},
            {"act": "第二幕 转折", "summary": "女孩进店，把湿透的伞靠在门边", "emotion": "试探与靠近", "duration": 18},
            {"act": "第三幕 落点", "summary": "一碗热面上桌，两人隔着蒸汽说话", "emotion": "释然", "duration": 20},
        ]
        script = Script(
            title=title, logline="雨夜最后一碗面，等的是一个不会来的人。",
            synopsis="打烊前的面馆，老板为一位淋雨的女孩多煮了一碗面。蒸汽升起来的时候，她说出了那个等了很久的名字。",
            tone="克制的温情", genre="微叙事概念片", target_duration=duration,
            narrator="低沉男声，慢速", style_prompt=style_prompt, music_mood="低频钢琴 + 雨声",
            characters=[Character(name="老板", look="五十岁男人，白色围裙，手上有面粉",
                                  look_en="a 50-year-old Chinese noodle shop owner in a stained white apron, weathered kind face"),
                        Character(name="女孩", look="二十多岁，湿透的米色风衣，短发",
                                  look_en="a woman in her twenties with short wet hair, soaked beige trench coat, tired eyes")],
            beats=beats,
        )
        raw = [
            ("第一幕 建立", 7, "雨夜老街，面馆灯牌忽明忽暗",
             "雨砸在青石板上，整条街只剩这一盏灯还亮着。", "缓慢推近",
             "Rain hammers a narrow old street at night; a single noodle-shop sign flickers warm orange, steam leaking from the doorway, reflections on wet stone.",
             "Slow push-in toward a tiny noodle shop on a rain-soaked old street at night, flickering warm sign, steam curling out, neon reflections, shallow depth of field."),
            ("第一幕 建立", 6, "店内，老板在案板前揉面",
             "他把面团按下去，又提起来，像在跟时间较劲。", "低角度固定镜头",
             "Inside the shop, a 50-year-old owner in a flour-dusted apron kneads dough under a single hanging bulb, steam and flour in the air.",
             "Static low-angle shot of a noodle shop owner kneading dough, flour dust floating in warm tungsten light, steam drifting across frame."),
            ("第一幕 建立", 6, "门外雨幕，一个身影站住",
             "门口的雨里，站着一个没打伞的人。", "隔着玻璃拍摄",
             "Through a rain-streaked window, a blurred figure stands still outside, backlit by a cold blue street lamp.",
             "Shot through a rain-covered window: a blurred silhouetted figure standing outside in the downpour, cold blue backlight, raindrops sliding down the glass."),
            ("第二幕 转折", 7, "女孩推门进来，风铃响",
             "门开了，风把雨气一起推进来。", "手持跟随",
             "A young woman in a soaked beige trench coat pushes the door open; wind chimes swing, rain light spills in.",
             "Handheld follow shot as a wet young woman in a beige trench coat steps into a small noodle shop, wind chimes swinging, rain light spilling across the floor."),
            ("第二幕 转折", 6, "她把伞靠在门边，坐下",
             "她把伞靠在门边，水滴了一路。", "中景横移",
             "She leans a dripping umbrella against the door frame and sits down at the counter, water pooling on the tiles.",
             "Slow lateral dolly as the woman leans a dripping umbrella against the door frame and sits at the counter, puddle spreading on the tiles."),
            ("第二幕 转折", 6, "老板抬眼，没说话，多抓了一把面",
             "老板没问，只多抓了一把面下锅。", "特写手部",
             "Close-up of the owner's floury hands pulling an extra portion of fresh noodles and dropping them into boiling water.",
             "Extreme close-up of flour-covered hands dropping fresh noodles into a rolling pot of boiling water, steam bursting up into the light."),
            ("第三幕 落点", 7, "一碗面推到她面前，蒸汽升起来",
             "面推过来的时候，热气糊住了她的眼睛。", "俯拍推近",
             "A steaming bowl of noodles slides across the worn wooden counter toward the woman; steam rises and softens her face.",
             "Overhead push-in on a steaming bowl of noodles sliding across a worn wooden counter to a young woman, steam rising and softening her tired face."),
            ("第三幕 落点", 7, "两人隔着蒸汽，她开口说了个名字",
             "她说了个名字，老板的手停在半空。", "双人中景，缓慢拉远",
             "Two-shot across the counter: the woman speaks through the steam, the owner's hand freezes mid-air holding a ladle.",
             "Slow pull-back two-shot across a noodle-shop counter, the woman speaking through rising steam, the owner frozen with a ladle in mid-air, warm tungsten light."),
        ]
        shots: List[Shot] = []
        for i, (act, dur, scene, line, cam, img, vid) in enumerate(raw, 1):
            shots.append(Shot(
                id=f"S{i:02d}", index=i, act=act, duration=dur, scene=scene,
                description=scene, camera=cam, dialogue=line, speaker="旁白",
                subtitle=line, image_prompt=img, video_prompt=vid,
                negative_prompt=self.s.comfy.negative, transition="cut",
            ))
        return script, self._normalize(shots, script, duration)
