import argparse
from pathlib import Path
from .editor import Options, edit

parser = argparse.ArgumentParser(description="Instagram 用の縦型動画を自動編集")
parser.add_argument("input", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--bgm", type=Path)
parser.add_argument("--bgm-rights-confirmed", action="store_true")
parser.add_argument("--no-subtitles", action="store_true")
parser.add_argument("--model", choices=["tiny", "base", "small", "medium"], default="base")
parser.add_argument("--font", default="Noto Sans CJK JP")
args = parser.parse_args()
if args.bgm and not args.bgm_rights_confirmed:
    parser.error("BGM の利用条件を確認し --bgm-rights-confirmed を指定してください")
try:
    edit(args.input, args.output, Options(model=args.model, subtitles=not args.no_subtitles, font=args.font), args.bgm, print)
except (ValueError, RuntimeError) as exc:
    parser.exit(1, f"エラー: {exc}\n")
