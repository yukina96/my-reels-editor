"""FFmpeg editing pipeline. All media stays on the local machine."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Options:
    model: str = "base"
    subtitles: bool = True
    silence_db: int = -35
    min_silence: float = 0.7
    padding: float = 0.15
    bgm_volume: float = 0.12
    font: str = "Noto Sans CJK JP"


def run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} が失敗しました:\n{result.stderr[-5000:]}")
    return result.stdout


def probe(path: Path) -> dict:
    return json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]))


def keep_ranges(log: str, duration: float, padding: float) -> list[tuple[float, float]]:
    """Remove detected silence, retaining a little room around speech."""
    silences = []
    start = None
    for match in re.finditer(r"silence_(start|end):\s*([\d.]+)", log):
        kind, value = match.group(1), float(match.group(2))
        if kind == "start":
            start = value
        elif start is not None:
            silences.append((start, value))
            start = None
    if start is not None:
        silences.append((start, duration))
    ranges = []
    cursor = 0.0
    for start, end in silences:
        cut_start = min(duration, max(cursor, start + padding))
        cut_end = min(duration, max(cut_start, end - padding))
        if cut_end <= cut_start:
            continue
        if cut_start - cursor > 0.04:
            ranges.append((cursor, cut_start))
        cursor = cut_end
    if duration - cursor > 0.04:
        ranges.append((cursor, duration))
    return ranges


def ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{cs:02}"


def subtitle_text(text: str) -> str:
    # Disallow ASS control injection from transcription, wrap Japanese text.
    text = text.replace("\\", "＼").replace("{", "（").replace("}", "）")
    text = " ".join(text.split())
    return r"\N".join(text[i:i + 18] for i in range(0, len(text), 18))


def write_ass(path: Path, segments: list[tuple[float, float, str]], font: str) -> None:
    if any(c in font for c in ",\n\r"):
        raise ValueError("フォント名にカンマや改行は使えません")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},64,&H00FFFFFF,&H0000D7FF,&H00222222,&H80000000,-1,0,0,0,100,100,1,0,1,4,2,2,90,90,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{subtitle_text(t)}\n"
             for a, b, t in segments if b > a and t.strip()]
    path.write_text(header + "".join(lines), encoding="utf-8")


def edit(source: Path, output: Path, options: Options | None = None,
         bgm: Path | None = None, progress: Callable[[str], None] = lambda _: None) -> Path:
    options = options or Options()
    source, output = source.resolve(), output.resolve()
    bgm = bgm.resolve() if bgm else None
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg と ffprobe をインストールしてください")
    if not source.is_file() or (bgm and not bgm.is_file()):
        raise ValueError("入力ファイルが見つかりません")
    if output == source or output == bgm or output.exists():
        raise ValueError("出力には、入力とは異なる未使用のファイル名を指定してください")
    if output.suffix.lower() != ".mp4":
        raise ValueError("出力ファイルの拡張子は .mp4 にしてください")
    if not (-60 <= options.silence_db <= -10 and 0.2 <= options.min_silence <= 5
            and 0 <= options.padding <= 0.5 and 0 <= options.bgm_volume <= 0.5):
        raise ValueError("編集設定が範囲外です")
    info = probe(source)
    if not any(s["codec_type"] == "video" for s in info["streams"]):
        raise ValueError("動画トラックがありません")
    if not any(s["codec_type"] == "audio" for s in info["streams"]):
        raise ValueError("音声のある動画を指定してください")
    if bgm and not any(s["codec_type"] == "audio" for s in probe(bgm)["streams"]):
        raise ValueError("BGM に音声トラックがありません")
    duration = float(info["format"]["duration"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reels-") as directory:
        work = Path(directory)
        progress("無音を検出しています")
        detected = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(source), "-vn", "-af",
                                   f"silencedetect=noise={options.silence_db}dB:d={options.min_silence}",
                                   "-f", "null", "-"], capture_output=True, text=True)
        if detected.returncode:
            raise RuntimeError(detected.stderr[-5000:])
        ranges = keep_ranges(detected.stderr, duration, options.padding)
        if not ranges or sum(b - a for a, b in ranges) < 0.5:
            raise ValueError("残せる音声がありません。無音判定のしきい値を下げてください")
        progress(f"{len(ranges)} 区間を結合しています")
        # Render independently to keep filter graphs bounded for longer footage.
        concat_lines = []
        for i, (a, b) in enumerate(ranges):
            part = work / f"part{i:05}.mp4"
            run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-ss", str(a), "-t", str(b - a),
                 "-map", "0:v:0", "-map", "0:a:0", "-vf",
                 "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                 "-threads", "2", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(part)])
            concat_lines.append(f"file '{part.name}'\n")
        (work / "parts.txt").write_text("".join(concat_lines))
        cut = work / "cut.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "1", "-i", str(work / "parts.txt"),
             "-c", "copy", str(cut)])
        subtitles = work / "captions.ass"
        if options.subtitles:
            progress("日本語を文字起こししています（初回はモデルをダウンロード）")
            from faster_whisper import WhisperModel
            wav = work / "speech.wav"
            run(["ffmpeg", "-y", "-v", "error", "-i", str(cut), "-vn", "-ar", "16000", "-ac", "1", str(wav)])
            try:
                model = WhisperModel(options.model, device="cpu", compute_type="int8", cpu_threads=2, num_workers=1)
            except Exception as exc:
                raise RuntimeError("音声認識モデルを読み込めません。初回は Hugging Face への接続とモデル保存先の空き容量が必要です。字幕オフでも編集できます。") from exc
            segments, _ = model.transcribe(str(wav), language="ja", beam_size=5, vad_filter=True)
            captions = [(s.start, s.end, s.text) for s in segments]
            if not captions:
                raise RuntimeError("文字起こし結果が空です。音声を確認するか字幕をオフにしてください")
            write_ass(subtitles, captions, options.font)
        progress("字幕・BGMを合成して MP4 を出力しています")
        args = ["ffmpeg", "-y", "-v", "error", "-i", str(cut)]
        if bgm:
            args += ["-stream_loop", "-1", "-i", str(bgm)]
            audio = (f"[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,asplit=2[voice][key];"
                     f"[1:a]volume={options.bgm_volume}[music];"
                     "[music][key]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=300[duck];"
                     "[voice][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
        else:
            audio = "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95[a]"
        args += ["-filter_complex", audio, "-map", "0:v:0", "-map", "[a]"]
        if options.subtitles:
            # Generated temporary path contains no user-controlled filter syntax.
            args += ["-vf", f"ass={subtitles}"]
        final = work / "result.mp4"
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "2",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(final)]
        run(args)
        # Exclusive creation prevents overwriting a file that appeared during rendering.
        with final.open("rb") as src, output.open("xb") as dst:
            shutil.copyfileobj(src, dst)
    progress("完成しました")
    return output
