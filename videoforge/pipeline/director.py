"""导演：把「写、画、拍、配、剪」串成可断点续跑的生产线。

设计原则：
- 每一步都落盘，崩在哪都能从断点继续，不重烧 token / 不重扣生成额度
- 生成类任务并发跑，状态更新加锁
- 单条镜头失败不拖垮全片，最后统一报告
"""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..config import Settings
from ..providers import Providers, ProviderError
from ..schemas import Project, Shot
from ..utils import Timer, ensure_dir, human_time, log, now_iso, warn
from .editor import Editor
from .writer import Screenwriter


class Director:
    def __init__(self, settings: Settings):
        self.s = settings
        self.providers = Providers(settings)
        self.writer = Screenwriter(settings)
        self.editor = Editor(settings)
        self._lock = threading.Lock()
        # 预算检查与扣费是两件事：并发镜头在真正花钱前要先把额度“占住”，
        # 否则 3 个 worker 会同时看到同一个余额，一起超额。
        self._budget_lock = threading.Lock()
        self._reservations: Dict[int, float] = {}
        self._res_seq = 0

    # ------------------------------------------------------------------ #
    # 预算护栏：单镜重拍 + 重试会实打实重复计费，得有人看着账本
    # ------------------------------------------------------------------ #
    def _costs(self, project: Project) -> Dict[str, Any]:
        return project.meta.setdefault("costs", {"total": 0.0, "entries": []})

    def _budget_check(self, project: Optional[Project], est_key: str, label: str) -> int:
        """检查并预占额度；返回预占 id，用完必须 `_release`。

        `project=None` 用于立项阶段（工程还没创建），此时没有并发，直接比价。
        """
        budget = float(self.s.pipeline.budget or 0.0)
        est = float(self.s.pipeline.prices.get(est_key, 0.0))
        if budget <= 0 or est <= 0:
            return 0
        if project is None:
            if est > budget:
                raise RuntimeError(
                    f"预算护栏：{label} 预估 ¥{est:.2f}，超过上限 ¥{budget:.2f}，已阻断。")
            return 0
        with self._budget_lock:
            used = float(self._costs(project).get("total", 0.0))
            reserved = sum(self._reservations.values())
            if used + reserved + est > budget:
                raise RuntimeError(
                    f"预算护栏：{label} 已计费约 ¥{used:.2f}、在途 ¥{reserved:.2f}，"
                    f"本次预估 ¥{est:.2f}，将超出上限 ¥{budget:.2f}，已阻断。"
                    f"（上调 VF_BUDGET 或删掉部分镜头后重试；明细见「成本小结」）")
            self._res_seq += 1
            rid = self._res_seq
            self._reservations[rid] = est
            return rid

    def _release(self, reservation: int) -> None:
        if not reservation:
            return
        with self._budget_lock:
            self._reservations.pop(reservation, None)

    def _charge(self, project: Project, provider: str, shot_id: str = "") -> None:
        est = float(self.s.pipeline.prices.get(provider, 0.0))
        if est <= 0:
            return
        with self._budget_lock:
            costs = self._costs(project)
            costs["entries"].append({"ts": now_iso(), "provider": provider,
                                     "shot": shot_id, "est": est})
            costs["total"] = round(float(costs.get("total", 0.0)) + est, 4)
            project.save()

    # ------------------------------------------------------------------ #
    # 立项
    # ------------------------------------------------------------------ #
    def plan(self, brief: str, *, title: str = "", duration: int = 60,
             aspect: str = "16:9", style: str = "", language: str = "zh",
             slug: str = "", shot_hint: int = 0, overwrite: bool = False) -> Project:
        from ..utils import safe_filename, slugify

        t0 = time.time()
        use_llm = self.s.deepseek.enabled and not self.s.pipeline.mock
        if use_llm:
            self._budget_check(None, "deepseek", "剧本生成")
        script, shots = self.writer.write(brief, title=title, duration=duration,
                                          aspect=aspect, style=style,
                                          language=language, shot_hint=shot_hint)
        name = slug or slugify(title or script.title)
        root = self.s.projects_root
        ensure_dir(root)
        pdir = root / safe_filename(name)
        if pdir.exists() and any(pdir.iterdir()) and not overwrite:
            pdir = root / f"{safe_filename(name)}-{time.strftime('%H%M%S')}"
        w, h = self._dims(aspect)
        project = Project.create(pdir, title=title or script.title, brief=brief,
                                 slug=pdir.name, aspect=aspect,
                                 resolution=f"{w}x{h}", fps=self.s.render.fps,
                                 mock=self.s.pipeline.mock)
        project.script = script
        project.shots = shots
        project.meta["timings"] = {"plan": round(time.time() - t0, 1)}
        project.meta["providers"] = {
            "script": "deepseek" if (self.s.deepseek.enabled and not self.s.pipeline.mock) else "mock",
            "video": "mock" if self.s.pipeline.mock else (
                "minimax" if self.s.minimax.enabled else "local"),
            "keyframe": "mock" if self.s.pipeline.mock else (
                "comfyui" if self.s.comfy.enabled else "local"),
            "tts": "mock" if self.s.pipeline.mock else (
                "minimax" if self.s.minimax.enabled else "auto"),
        }
        project.save()
        if use_llm:
            self._charge(project, "deepseek", "plan")
        self._write_storyboard_md(project)
        log(f"项目已创建：{pdir}  《{project.title}》 {len(shots)} 镜 / {project.total_duration}s")
        return project

    @staticmethod
    def _dims(aspect: str) -> tuple[int, int]:
        table = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080),
                 "4:3": (1440, 1080), "21:9": (2560, 1080), "3:4": (1080, 1440)}
        return table.get(aspect, (1920, 1080))

    def _write_storyboard_md(self, project: Project) -> None:
        sc = project.script
        lines = [f"# {project.title}", "",
                 f"> {sc.logline}", "",
                 f"- 类型：{sc.genre or '-'}　基调：{sc.tone or '-'}　目标时长：{sc.target_duration}s",
                 f"- 画幅：{project.aspect}　风格：{sc.style_prompt or '-'}", "",
                 "## 故事梗概", "", sc.synopsis or "-", "", "## 人物"]
        for c in sc.characters:
            lines.append(f"- **{c.name}**：{c.look}　`{c.look_en}`")
        if not sc.characters:
            lines.append("- （无角色）")
        lines += ["", "## 分镜表", "",
                  "| # | 时长 | 场景 | 运镜 | 台词 | 转场 |",
                  "|---|------|------|------|------|------|"]
        for s in project.shots:
            line = (s.dialogue or s.description).replace("|", "／")
            lines.append(f"| {s.id} | {s.duration}s | {s.scene[:24]} | {s.camera[:10]} | "
                         f"{line[:36]} | {s.transition} |")
        lines += ["", "## 提示词（生产用）", ""]
        for s in project.shots:
            lines += [f"**{s.id}**", "", f"- image: `{s.image_prompt}`",
                      f"- video: `{s.video_prompt}`", ""]
        project.path("storyboard.md").write_text("\n".join(lines), encoding="utf-8")

    # ------------------------------------------------------------------ #
    # 配音
    # ------------------------------------------------------------------ #
    def voice(self, project: Project, shots: Optional[Sequence[Shot]] = None,
              force: bool = False) -> Dict[str, int]:
        targets = [s for s in (shots or project.shots) if (force or not s.voiced) and s.text]
        stats = {"ok": 0, "fail": 0, "silent": 0}
        if not targets:
            return stats
        t0 = time.time()
        log(f"开始配音：{len(targets)} 条")
        for shot in targets:
            out = project.path("audio") / f"{shot.id}.mp3"
            reservation = 0
            if self.s.minimax.enabled and not self.s.pipeline.mock:
                reservation = self._budget_check(project, "minimax_tts", f"{shot.id} 配音")
            try:
                dur, engine = self.providers.voice.synthesize(
                    shot.text, out, voice_id=shot.speaker if shot.speaker not in ("", "旁白") else "",
                    speed=None)
                with self._lock:
                    shot.audio = str(out)
                    shot.audio_duration = dur
                    if engine == "silent":
                        stats["silent"] += 1
                    else:
                        stats["ok"] += 1
                    if shot.status == "pending":
                        shot.status = "voiced"
                    shot.updated_at = now_iso()
                if engine == "minimax":
                    self._charge(project, "minimax_tts", shot.id)
                project.save()
            except Exception as exc:
                stats["fail"] += 1
                warn(f"{shot.id} 配音失败：{exc}")
                with self._lock:
                    shot.error = f"tts: {exc}"[:400]
            finally:
                self._release(reservation)
        log(f"配音完成 ok={stats['ok']} silent={stats['silent']} fail={stats['fail']} "
            f"({human_time(time.time() - t0)})")
        return stats

    # ------------------------------------------------------------------ #
    # 关键帧
    # ------------------------------------------------------------------ #
    def keyframes(self, project: Project, shots: Optional[Sequence[Shot]] = None,
                  force: bool = False) -> Dict[str, int]:
        targets = [s for s in (shots or project.shots) if force or not s.keyframe]
        if not targets:
            return {"ok": 0, "fail": 0}

        def work(shot: Shot) -> None:
            out = project.path("keyframes") / f"{shot.id}.png"
            result = self.providers.keyframe(shot, out)
            with self._lock:
                if result.ok:
                    shot.keyframe = result.path
                    shot.provider = result.provider
                    if shot.status == "pending":
                        shot.status = "keyframe"
                    shot.error = ""
                else:
                    shot.status = "failed"
                    shot.error = "keyframe: 未产出文件"
                shot.updated_at = now_iso()
                project.save()

        stats = self._run_batch(project, targets, work, "出关键帧")
        return stats

    # ------------------------------------------------------------------ #
    # 镜头
    # ------------------------------------------------------------------ #
    def clips(self, project: Project, shots: Optional[Sequence[Shot]] = None,
              force: bool = False) -> Dict[str, int]:
        targets = [s for s in (shots or project.shots) if force or not s.clip]
        if not targets:
            return {"ok": 0, "fail": 0}

        def work(shot: Shot) -> None:
            out = project.path("clips") / f"{shot.id}.mp4"
            kf = Path(shot.keyframe) if shot.keyframe else None
            reservation = 0
            if self.s.minimax.enabled and not self.s.pipeline.mock:
                reservation = self._budget_check(project, "minimax_video", f"{shot.id} 镜头")
            try:
                result = self.providers.clip(shot, out, keyframe=kf)
                with self._lock:
                    if result.ok:
                        shot.clip = result.path
                        shot.provider = result.provider
                        shot.task_id = result.task_id
                        shot.clip_duration = result.duration
                        shot.seed = result.seed or shot.seed
                        shot.status = "rendered"
                        shot.error = ""
                        shot.attempts = 0
                    else:
                        shot.status = "failed"
                        shot.error = "clip: 未产出文件"
                        shot.attempts += 1
                    shot.updated_at = now_iso()
                    project.save()
                if result.ok and "minimax" in (result.provider or ""):
                    self._charge(project, "minimax_video", shot.id)
            finally:
                self._release(reservation)

        return self._run_batch(project, targets, work, "生成镜头")

    # ------------------------------------------------------------------ #
    # 批量执行器
    # ------------------------------------------------------------------ #
    def _run_batch(self, project: Project, targets: List[Shot], fn, label: str) -> Dict[str, int]:
        workers = max(1, min(self.s.pipeline.max_workers, len(targets)))
        stats = {"ok": 0, "fail": 0}
        t0 = time.time()
        log(f"开始{label}：{len(targets)} 条，并发 {workers}")
        if workers == 1:
            for shot in targets:
                try:
                    fn(shot)
                    stats["ok" if shot.status != "failed" else "fail"] += 1
                except Exception as exc:
                    stats["fail"] += 1
                    warn(f"{shot.id} {label}失败：{exc}")
                    with self._lock:
                        shot.status = "failed"
                        shot.error = f"{label}: {exc}"[:400]
                        shot.attempts += 1
                        shot.updated_at = now_iso()
                    project.save()
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vf") as pool:
                futures = {pool.submit(fn, shot): shot for shot in targets}
                for fut in as_completed(futures):
                    shot = futures[fut]
                    try:
                        fut.result()
                        stats["ok" if shot.status != "failed" else "fail"] += 1
                    except Exception as exc:
                        stats["fail"] += 1
                        warn(f"{shot.id} {label}失败：{exc}")
                        with self._lock:
                            shot.status = "failed"
                            shot.error = f"{label}: {exc}"[:400]
                            shot.attempts += 1
                            shot.updated_at = now_iso()
                        project.save()
        elapsed = time.time() - t0
        log(f"{label}结束 ok={stats['ok']} fail={stats['fail']} 用时 {human_time(elapsed)}")
        project.meta.setdefault("timings", {})[label] = round(elapsed, 1)
        project.save()
        return stats

    # ------------------------------------------------------------------ #
    # 一键渲染
    # ------------------------------------------------------------------ #
    def render(self, project: Project, *, force: bool = False, with_voice: bool = True,
               with_keyframes: bool = True, shots: Optional[Sequence[Shot]] = None) -> Dict:
        targets = [s for s in (shots or project.shots) if s.enabled]
        t0 = time.time()
        # 配音先行：语速/时长决定镜头该多长，避免音画错位后再返工
        if with_voice:
            self.voice(project, targets, force=force)
            adjusted = self._fit_durations(targets)
            if adjusted:
                log(f"按配音时长调整了 {adjusted} 条镜头时长")
        if with_keyframes:
            self.keyframes(project, targets, force=force)
        self.clips(project, targets, force=force)

        failed = [s.id for s in targets if s.status == "failed"]
        if failed:
            warn(f"失败镜头：{', '.join(failed)}（可 revise 重拍或 reduce 后重跑）")
        project.meta.setdefault("timings", {})["render_total"] = round(time.time() - t0, 1)
        project.save()
        return {"ok": len(targets) - len(failed), "failed": failed,
                "elapsed": round(time.time() - t0, 1)}

    def _fit_durations(self, shots: Sequence[Shot]) -> int:
        """配音比镜头长的，镜头跟着长；留 0.4s 余量，最长 15s（H3 上限）。"""
        count = 0
        for s in shots:
            if not s.audio_duration or s.clip:
                continue
            need = int(math.ceil(s.audio_duration + 0.45))
            new = max(4, min(15, need))
            if new > s.duration:
                s.duration = new
                count += 1
        return count

    # ------------------------------------------------------------------ #
    # 合成
    # ------------------------------------------------------------------ #
    def assemble(self, project: Project, force: bool = False) -> Dict:
        project.save()
        return self.editor.assemble(project, force=force)

    # ------------------------------------------------------------------ #
    # 重拍单条
    # ------------------------------------------------------------------ #
    def revise(self, project: Project, shot_id: str, note: str = "") -> Shot:
        shot = project.shot(shot_id)
        if not shot:
            raise ValueError(f"没有这个镜头：{shot_id}")
        if note:
            from ..llm import DeepSeek
            llm = DeepSeek(self.s.deepseek)
            if llm.available and not self.s.pipeline.mock:
                reservation = self._budget_check(project, "deepseek", f"{shot_id} 重拍改写")
                try:
                    msg = llm.json([
                        {"role": "system", "content": "你是分镜提示词医生。只改提示词，不改故事。"},
                        {"role": "user", "content":
                            f"原视频提示词：\n{shot.video_prompt}\n\n原关键帧提示词：\n{shot.image_prompt}\n\n"
                            f"导演修改意见：{note}\n\n"
                            "请输出 JSON：{\"video_prompt\": \"英文，单镜头单动作，70词内\", "
                            "\"image_prompt\": \"英文静帧提示词\", \"reason\": \"一句中文说明\"}"},
                    ], temperature=0.9, max_tokens=900)
                    shot.video_prompt = str(msg.get("video_prompt") or shot.video_prompt)
                    shot.image_prompt = str(msg.get("image_prompt") or shot.image_prompt)
                    self._charge(project, "deepseek", f"{shot_id}:revise")
                finally:
                    self._release(reservation)
                log(f"{shot.id} 提示词已按意见改写：{msg.get('reason', '')}")
            else:
                shot.video_prompt = f"{shot.video_prompt} {note}".strip()
        shot.keyframe = ""
        shot.clip = ""
        shot.status = "pending"
        shot.error = ""
        shot.seed = 0
        project.save()
        self.keyframes(project, [shot], force=True)
        self.clips(project, [shot], force=True)
        return shot
