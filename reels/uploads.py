"""Transient media processing; never retain media paths in shared state."""
from pathlib import Path
import tempfile

from .editor import Options, edit, probe

MAX_UPLOAD = 50 * 1024 * 1024
MAX_DURATION = 60
MAX_RESULT = 50 * 1024 * 1024


def render_upload(video: bytes, suffix: str, options: Options, music: bytes | None = None,
                  music_suffix: str = '.wav', progress=lambda _: None) -> bytes:
    if not video or len(video) > MAX_UPLOAD or (music is not None and len(music) > MAX_UPLOAD):
        raise ValueError('動画・BGM はそれぞれ 50MB 以下にしてください')
    if suffix not in {'.mp4', '.mov', '.mkv', '.webm', '.avi'}:
        raise ValueError('対応する動画形式を選んでください')
    if music is not None and music_suffix not in {'.mp3', '.wav', '.m4a', '.ogg', '.flac'}:
        raise ValueError('対応する BGM 形式を選んでください')
    # TemporaryDirectory is unique per operation and mode 0700 on Linux.
    with tempfile.TemporaryDirectory(prefix='reels-private-') as directory:
        root = Path(directory)
        source = root / ('input' + suffix)
        source.write_bytes(video)
        if float(probe(source)['format']['duration']) > MAX_DURATION:
            raise ValueError('無料クラウド向けに動画は 60 秒以下にしてください')
        bgm = None
        if music is not None:
            bgm = root / ('music' + music_suffix)
            bgm.write_bytes(music)
        output = root / 'result.mp4'
        edit(source, output, options, bgm, progress)
        if output.stat().st_size > MAX_RESULT:
            raise ValueError('完成動画が 50MB を超えました。素材を短くしてください')
        return output.read_bytes()
