"""剪辑台：把零散片段拼成能发的成片。

两条路线：
- 全 hard cut → concat demuxer（快）
- 有转场 → 归一化后走 xfade 链（真交叉/划像/黑场，时间轴按重叠秒数收缩）

四段式：
  1) 归一化：分辨率/帧率/SAR、变速、镜像、调色、精确时长
  2) 拼接：concat 或 xfade
  3) 声轨：逐条配音对位 + 音量 + BGM + 响度归一
  4) 定版：字幕/文字层烧录 + 淡入淡出 + 混流
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..config import RenderConfig, Settings
from ..encode import video_args
from ..editing import color_chain, shot_transition_duration, transition_name, transition_xfade
from ..schemas import Project, Shot
from ..utils import clamp, ensure_dir, ffmpeg, human_time, log, probe_duration, warn
from .subtitles import write_ass, write_srt, write_title_ass

# 硬切在 xfade 链里用极短融合占位（视觉上就是切）
CUT_EPS = 0.04


class Editor:
    def __init__(self, settings: Settings):
        self.s = settings
        self.cfg: RenderConfig = settings.render

    # ------------------------------------------------------------------ #
    # 1) 归一化
    # ------------------------------------------------------------------ #
    def normalize(self, src: Path, dst: Path, shot: Shot, *, fade: bool = False) -> float:
        cfg = self.cfg
        ensure_dir(dst.parent)
        dur = max(0.5, float(shot.duration))
        speed = clamp(float(shot.speed or 1.0), 0.25, 4.0)
        need = dur * speed
        trim = max(0.0, float(shot.trim_start or 0.0))
        src_dur = probe_duration(src)
        if src_dur and src_dur + 0.25 < need + trim:
            warn(f"{Path(src).name} 只有 {src_dur:.1f}s，变速后需 {need + trim:.1f}s，将复制末帧补齐")

        vf: List[str] = []
        if shot.reverse:
            vf.append("reverse")
        if abs(speed - 1.0) > 1e-3:
            vf.append(f"setpts=PTS/{speed:.4f}")
        vf.append(f"fps={cfg.fps}")
        vf.append(f"scale={cfg.width}:{cfg.height}:force_original_aspect_ratio=decrease")
        vf.append(f"pad={cfg.width}:{cfg.height}:(ow-iw)/2:(oh-ih)/2:color=black")
        vf.append("setsar=1")
        if shot.mirror:
            vf.append("hflip")
        vf.extend(color_chain(shot))
        vf.append("tpad=stop_mode=clone:stop_duration=4")
        if fade:
            f = min(0.3, dur / 5)
            vf.append(f"fade=t=in:st=0:d={f:.2f}")
            vf.append(f"fade=t=out:st={max(0, dur - f):.2f}:d={f:.2f}")
        vf.append("format=yuv420p")

        seek = ["-ss", f"{trim:.3f}"] if trim > 0.01 else []
        ffmpeg([
            *seek, "-t", f"{need:.3f}", "-i", str(src), "-vf", ",".join(vf),
            "-r", str(cfg.fps), "-t", f"{dur:.3f}",
            *video_args(cfg, 20, fast=True), "-an", str(dst),
        ], timeout=900)
        return probe_duration(dst) or dur

    # ------------------------------------------------------------------ #
    # 转场链
    # ------------------------------------------------------------------ #
    def xfade_concat(self, segments: Sequence[dict], dst: Path, *, cwd: Path) -> Path:
        """segments: {path, duration, xfade, td}，转场由「后一条」决定。"""
        cfg = self.cfg
        n = len(segments)
        args: List[str] = []
        for seg in segments:
            args += ["-i", str(seg["path"])]

        graph: List[str] = []
        labels: List[str] = []
        for i in range(n):
            graph.append(f"[{i}:v]settb=AVTB,fps={cfg.fps},format=yuv420p[v{i}]")
            labels.append(f"[v{i}]")

        cur = labels[0]
        merged = float(segments[0]["duration"])
        for i in range(1, n):
            # 硬切也必须给 xfade 一个正的 duration，否则它会截断后续所有片段
            td = max(CUT_EPS, float(segments[i]["td"]))
            name = segments[i]["xfade"] or "fade"
            offset = max(0.0, merged - td)
            out = f"[x{i}]"
            graph.append(f"{cur}{labels[i]}xfade=transition={name}:duration={td:.3f}:"
                         f"offset={offset:.3f}{out}")
            cur = out
            merged = merged + float(segments[i]["duration"]) - td

        ensure_dir(dst.parent)
        ffmpeg([*args, "-filter_complex", ";".join(graph), "-map", cur,
                *video_args(cfg, 19), "-an", "-movflags", "+faststart", str(dst)],
               timeout=3600)
        return dst

    # ------------------------------------------------------------------ #
    # 卡片
    # ------------------------------------------------------------------ #
    def card(self, dst: Path, duration: float, lines: Sequence[tuple[str, int]]) -> Optional[Path]:
        cfg = self.cfg
        ensure_dir(dst.parent)
        work = dst.parent
        vf_parts: List[str] = []
        tmp_files: List[Path] = []
        try:
            total_h = sum(size + 28 for _, size in lines)
            y = f"(h-{total_h})/2"
            for i, (text, size) in enumerate(lines):
                tf = work / f"_card_{dst.stem}_{i}.txt"
                tf.write_text(text, encoding="utf-8")
                tmp_files.append(tf)
                off = sum(s + 28 for _, s in lines[:i])
                vf_parts.append(
                    f"drawtext=textfile='{tf.name}':fontcolor=white:fontsize={size}:"
                    f"x=(w-text_w)/2:y={y}+{off}:shadowcolor=black@0.6:shadowx=2:shadowy=2"
                )
            vf_parts.append(f"fade=t=in:st=0:d=0.5,fade=t=out:st={max(0, duration - 0.6):.2f}:d=0.6")
            vf_parts.append("format=yuv420p")
            ffmpeg([
                "-f", "lavfi", "-i", f"color=c=0x0C0C0E:s={cfg.width}x{cfg.height}:r={cfg.fps}",
                "-t", f"{duration:.2f}", "-vf", ",".join(vf_parts),
                *video_args(cfg, 20, fast=True), "-an", str(dst),
            ], timeout=180, cwd=work)
            return dst
        except Exception as exc:
            warn(f"卡片文字渲染失败（可能缺中文字体），退化为纯色卡：{exc}")
            try:
                ffmpeg(["-f", "lavfi", "-i",
                        f"color=c=0x0C0C0E:s={cfg.width}x{cfg.height}:r={cfg.fps}:d={duration:.2f}",
                        *video_args(cfg, 20, fast=True), "-an", str(dst)],
                       timeout=120)
                return dst
            except Exception as exc2:
                warn(f"连纯色卡都失败，跳过：{exc2}")
                return None
        finally:
            for tf in tmp_files:
                tf.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    # 2) 拼接（纯硬切快速路径）
    # ------------------------------------------------------------------ #
    def concat(self, files: Sequence[Path], dst: Path, cwd: Path) -> Path:
        ensure_dir(dst.parent)
        listing = cwd / "_concat.txt"
        listing.write_text(
            "\n".join(f"file '{f.relative_to(cwd).as_posix() if f.is_absolute() else f.as_posix()}'"
                      for f in files), encoding="utf-8")
        try:
            ffmpeg(["-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy",
                    "-movflags", "+faststart", str(dst)], timeout=900, cwd=cwd)
        except Exception:
            warn("无损拼接失败，改用重编码拼接")
            ffmpeg(["-f", "concat", "-safe", "0", "-i", listing.name,
                    *video_args(self.cfg, 20, fast=True), str(dst)], timeout=1800, cwd=cwd)
        listing.unlink(missing_ok=True)
        return dst

    # ------------------------------------------------------------------ #
    # 3) 声轨
    # ------------------------------------------------------------------ #
    def build_audio(self, project: Project, placements: Sequence[tuple[Path, float, float, float]],
                    total: float, dst: Path) -> Optional[Path]:
        """placements: [(音频, 起始秒, 目标时长, 音量)]"""
        ensure_dir(dst.parent)
        args: List[str] = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        filters: List[str] = []
        labels: List[str] = ["[0:a]"]
        idx = 1
        for audio, start, target, volume in placements:
            if not audio or not Path(audio).exists():
                continue
            src_dur = probe_duration(audio)
            chain = [f"[{idx}:a]aresample=48000",
                     "aformat=sample_fmts=fltp:channel_layouts=stereo"]
            if src_dur > target + 0.05:
                tempo = min(1.12, src_dur / max(0.5, target))
                chain.append(f"atempo={tempo:.3f}")
            chain.append(f"volume={clamp(volume, 0, 2):.3f}")
            chain.append(f"adelay={int(start * 1000)}|{int(start * 1000)}")
            filters.append(",".join(chain) + f"[v{idx}]")
            labels.append(f"[v{idx}]")
            args += ["-i", str(audio)]
            idx += 1

        bgm = project.bgm or self.cfg.bgm
        if bgm and Path(bgm).exists():
            args += ["-stream_loop", "-1", "-i", str(bgm)]
            filters.append(
                f"[{idx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"atrim=0:{total:.3f},volume={self.cfg.bgm_volume},"
                f"afade=t=in:st=0:d=1.2,"
                f"afade=t=out:st={max(0, total - 2.5):.2f}:d=2.5[b0]"
            )
            labels.append("[b0]")
            idx += 1

        if len(labels) == 1:
            filters.append("[0:a]volume=0.0[mixout]")
        else:
            filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:"
                           f"duration=first:dropout_transition=0[mixout]")
        ffmpeg([*args, "-filter_complex", ";".join(filters), "-map", "[mixout]",
                "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", str(dst)],
               timeout=900)
        return dst if Path(dst).exists() else None

    def silence_track(self, total: float, dst: Path) -> Path:
        ensure_dir(dst.parent)
        ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{total:.3f}",
                "-c:a", "aac", "-b:a", "128k", str(dst)], timeout=300)
        return dst

    # ------------------------------------------------------------------ #
    # 4) 定版
    # ------------------------------------------------------------------ #
    def finalize(self, video: Path, audio: Path, dst: Path, *, total: float,
                 ass: Optional[Path] = None, title_ass: Optional[Path] = None) -> Path:
        cfg = self.cfg
        ensure_dir(dst.parent)
        work = video.parent
        filters = []
        if ass and ass.exists():
            filters.append(f"ass={ass.name}")
        if title_ass and title_ass.exists():
            filters.append(f"ass={title_ass.name}")
        filters.append("fade=t=in:st=0:d=0.6")
        filters.append(f"fade=t=out:st={max(0, total - 0.9):.2f}:d=0.9")
        filters.append("format=yuv420p")
        afilter = ["loudnorm=I=-16:TP=-1.5:LRA=11"] if cfg.loudnorm else []
        args = ["-i", video.name, "-i", audio.name,
                "-filter_complex", f"[0:v]{','.join(filters)}[v]"
                                   + (f";[1:a]{','.join(afilter)}[a]" if afilter else ""),
                "-map", "[v]", "-map", "[a]" if afilter else "1:a",
                *video_args(cfg), "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-t", f"{total:.3f}", str(dst)]
        ffmpeg(args, timeout=3600, cwd=work)
        return dst

    # ------------------------------------------------------------------ #
    # 总装
    # ------------------------------------------------------------------ #
    def _cache_key(self, shot: Shot) -> str:
        raw = "|".join(str(x) for x in (
            shot.duration, shot.trim_start, shot.speed, int(shot.reverse), int(shot.mirror),
            shot.filter_preset, shot.brightness, shot.contrast, shot.saturation,
            shot.temperature, self.cfg.width, self.cfg.height, self.cfg.fps,
        ))
        return hashlib.sha1(raw.encode()).hexdigest()[:8]

    def assemble(self, project: Project, *, force: bool = False) -> Dict[str, object]:
        cfg = self.cfg
        cache = ensure_dir(project.path("cache"))
        out_dir = ensure_dir(project.path("output"))
        norm_dir = ensure_dir(cache / "norm")
        project.save()

        shots = [s for s in project.shots if s.enabled and s.clip and Path(s.clip).exists()]
        missing = [s.id for s in project.shots
                   if s.enabled and not (s.clip and Path(s.clip).exists())]
        excluded = [s.id for s in project.shots if not s.enabled]
        if not shots:
            raise RuntimeError("没有任何可用片段，先跑 render 再合成")
        if missing:
            warn(f"以下镜头缺片段，成片会跳过它们：{', '.join(missing)}")

        # --- 卡片 ---
        title_card = end_card = None
        title_dur = end_dur = 0.0
        if cfg.title_card:
            title_card = self.card(cache / "card_title.mp4", 3.2,
                                   [(project.title or "未命名", 76),
                                    (project.script.logline[:28] or "VisionForge", 30)])
            if title_card:
                title_dur = probe_duration(title_card) or 3.2
        if cfg.end_card:
            end_card = self.card(cache / "card_end.mp4", 3.4,
                                 [("VisionForge", 58),
                                  ("DeepSeek × MiniMax H3 × ComfyUI", 26)])
            if end_card:
                end_dur = probe_duration(end_card) or 3.4

        # --- 归一化 ---
        norm_files: List[Path] = []
        durations: List[float] = []
        for shot in shots:
            key = self._cache_key(shot)
            dst = norm_dir / f"{shot.id}_{key}.mp4"
            if force or not dst.exists() or dst.stat().st_mtime < Path(shot.clip).stat().st_mtime:
                try:
                    dur = self.normalize(Path(shot.clip), dst, shot)
                except Exception as exc:
                    warn(f"{shot.id} 归一化失败，跳过：{exc}")
                    continue
            else:
                dur = probe_duration(dst) or float(shot.duration)
            shot.clip_duration = round(dur, 3)
            norm_files.append(dst)
            durations.append(dur)
            speed_txt = f" {shot.speed:.2f}x" if abs(shot.speed - 1) > 0.01 else ""
            log(f"归一化 {shot.id}{speed_txt} → {dur:.2f}s")
        if not norm_files:
            raise RuntimeError("归一化后没有可拼接片段")

        valid = shots[:len(norm_files)]
        default_td = 0.4

        # --- 组装段列表（卡片 + 镜头）---
        segments: List[dict] = []
        if title_card and title_dur > 0:
            segments.append({"path": title_card, "duration": title_dur, "shot": None,
                             "transition": "fade", "xfade": "fade", "td": 0.5})
        for shot, path, dur in zip(valid, norm_files, durations):
            name = transition_name(shot)
            xf = transition_xfade(shot)
            td = shot_transition_duration(shot, default_td) if xf else 0.0
            segments.append({"path": path, "duration": dur, "shot": shot,
                             "transition": name, "xfade": xf, "td": td})
        if end_card and end_dur > 0:
            segments.append({"path": end_card, "duration": end_dur, "shot": None,
                             "transition": "fade", "xfade": "fade", "td": 0.6})

        # 转场由「后一段」决定；硬切在 xfade 模式下用极小重叠
        use_xfade = any(seg["xfade"] for seg in segments[1:])
        overlaps: List[float] = []
        for i in range(len(segments) - 1):
            nxt = segments[i + 1]
            td = float(nxt["td"]) if nxt["xfade"] else (CUT_EPS if use_xfade else 0.0)
            td = min(td, float(segments[i]["duration"]) * 0.45, float(nxt["duration"]) * 0.45)
            overlaps.append(round(td, 4))
        overlaps.append(0.0)

        starts: List[float] = [0.0]
        for i in range(1, len(segments)):
            starts.append(round(starts[i - 1] + float(segments[i - 1]["duration"])
                                - overlaps[i - 1], 4))
        total = round(starts[-1] + float(segments[-1]["duration"]), 3)

        # --- 拼接 ---
        silent = cache / "video_silent.mp4"
        if use_xfade:
            try:
                self.xfade_concat(segments, silent, cwd=cache)
                log(f"转场拼接完成：{sum(1 for s in segments[1:] if s['xfade'])} 处转场，"
                    f"重叠 {sum(overlaps):.2f}s")
            except Exception as exc:
                warn(f"转场链失败，退回硬切拼接：{exc}")
                use_xfade = False
                silent = self.concat([s["path"] for s in segments], cache / "video_silent.mp4", cache)
                starts, total = self._recompute_hard_cut(segments)
        else:
            silent = self.concat([s["path"] for s in segments], silent, cache)

        # 镜头起点（剔除卡片偏移）
        offset_idx = 1 if title_card else 0
        shot_starts = [starts[i + offset_idx] for i in range(len(valid))]
        shot_durs = durations

        # --- 声轨 ---
        placements: List[tuple[Path, float, float, float]] = []
        for shot, st, dur in zip(valid, shot_starts, shot_durs):
            if shot.audio and Path(shot.audio).exists() and shot.audio_duration > 0:
                placements.append((Path(shot.audio), st + 0.1, max(0.4, dur - 0.1),
                                   float(shot.volume or 1.0)))
        audio = (self.build_audio(project, placements, total, cache / "mix.m4a")
                 if placements else self.silence_track(total, cache / "mix.m4a"))

        # --- 字幕 / 文字层 ---
        ass_path = None
        title_ass = None
        srt_path = project.path("output") / "subtitles.srt"
        if use_xfade:
            write_srt(srt_path, valid, starts=shot_starts, durations=shot_durs)
            if cfg.subtitle_enabled:
                ass_path = write_ass(cache / "subtitles.ass", valid, cfg,
                                     starts=shot_starts, durations=shot_durs)
            if any(s.title_text.strip() for s in valid):
                title_ass = write_title_ass(cache / "titles.ass", valid, cfg,
                                            starts=shot_starts, durations=shot_durs)
        else:
            write_srt(srt_path, valid, durations=shot_durs)
            if cfg.subtitle_enabled:
                ass_path = write_ass(cache / "subtitles.ass", valid, cfg, durations=shot_durs)
            if any(s.title_text.strip() for s in valid):
                title_ass = write_title_ass(cache / "titles.ass", valid, cfg, durations=shot_durs)

        final = out_dir / f"{project.slug}.mp4"
        self.finalize(silent, audio, final, total=total, ass=ass_path, title_ass=title_ass)

        info = probe_duration(final)
        project.output = str(final)
        project.meta["final_duration"] = round(info, 2)
        project.meta["final_size_mb"] = round(final.stat().st_size / 1048576, 1) if final.exists() else 0
        project.meta["assembled_at"] = project.updated_at
        project.meta["transitions"] = sum(1 for s in valid if transition_xfade(s))
        project.save()
        log(f"成片完成 {final.name} | {human_time(info)} | {project.meta['final_size_mb']}MB",
            tag="edit")
        return {
            "output": str(final),
            "duration": round(info, 2),
            "size_mb": project.meta["final_size_mb"],
            "subtitles": str(srt_path),
            "shots_included": len(norm_files),
            "shots_missing": missing,
            "shots_excluded": excluded,
            "transitions": project.meta["transitions"],
        }

    @staticmethod
    def _recompute_hard_cut(segments: Sequence[dict]) -> Tuple[List[float], float]:
        starts, total = [0.0], 0.0
        for seg in segments:
            total += float(seg["duration"])
            starts.append(round(total, 4))
        return starts[:-1], round(total, 4)
