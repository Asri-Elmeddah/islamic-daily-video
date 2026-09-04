# -*- coding: utf-8 -*-
"""
generate_video_poster.py

نقطة الدخول: يقرا config.json، يختار سورة تلقائيا (بلا تكرار)،
يبني مقطع صوتي/نصي بحدود max_video_seconds، يرندر الفيديو، ويسجل
فالـ history.

USAGE:
    python generate_video_poster.py
    python generate_video_poster.py --surah 36     (تحكم يدوي فالسورة)
"""

import argparse
import json
import sys
from pathlib import Path

import requests

from quran_source import (
    pick_surah_number,
    fetch_surah_meta,
    build_clip_plan,
    record_history,
)
from video_renderer import RenderConfig, render_video, seconds_to_label

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--surah", type=int, default=None, help="رقم سورة يدوي (1-114)")
    args = parser.parse_args()

    cfg_dict = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

    history_file = ROOT / cfg_dict["history_file"]
    cache_dir = ROOT / cfg_dict["cache_dir"]
    output_dir = ROOT / cfg_dict["output_dir"]

    surah_number = pick_surah_number(
        history_file, override=args.surah or cfg_dict.get("override_surah_number")
    )

    print(f"[*] السورة المختارة: {surah_number}")

    try:
        surah_meta = fetch_surah_meta(surah_number)
        print(f"[*] {surah_meta['name_ar']} — {surah_meta['ayahs_count']} آية")

        print("[*] جاري تحميل الصوت وبناء المقطع...")
        plan, total_duration = build_clip_plan(
            surah_meta,
            edition=cfg_dict["reciter_edition"],
            bitrate=cfg_dict["audio_bitrate"],
            cache_dir=cache_dir,
            max_seconds=cfg_dict["max_video_seconds"],
        )
    except requests.exceptions.RequestException as exc:
        print("\n[✗] ماقدرناش نتواصلو مع api.alquran.cloud بعد عدة محاولات.")
        print(f"    السبب: {exc.__class__.__name__}: {exc}")
        print("\n    الأسباب المحتملة:")
        print("    - الاتصال بالإنترنت ضعيف أو منقطع مؤقتا")
        print("    - فايروول/أنتيفيروس أو VPN كايحجب الاتصال بـ api.alquran.cloud")
        print("    - السيرفر ديال AlQuran.cloud فيه ضغط مؤقت")
        print("\n    الحل: تأكد من الاتصال وعاود جرب:")
        print(f"        python {Path(__file__).name}" + (f" --surah {args.surah}" if args.surah else ""))
        sys.exit(1)

    print(
        f"[*] المقطع: آية {plan[0]['number_in_surah']} "
        f"إلى آية {plan[-1]['number_in_surah']} "
        f"— المدة: {seconds_to_label(total_duration)}"
    )

    render_cfg = RenderConfig(cfg_dict, ROOT)

    output_path = output_dir / f"surah_{surah_number}_{plan[0]['number_in_surah']}-{plan[-1]['number_in_surah']}.mp4"
    work_dir = ROOT / "work"

    print("[*] جاري الرندر...")
    final_path = render_video(
        render_cfg, surah_meta, plan, total_duration, output_path, work_dir
    )

    record_history(history_file, surah_meta, plan)

    print(f"[OK] الفيديو جاهز: {final_path}")


if __name__ == "__main__":
    main()
