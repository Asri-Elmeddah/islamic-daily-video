# -*- coding: utf-8 -*-
"""
Generate one Quran video and publish it as Instagram Reels
Wraps generate_video_poster.py + instagram_reels_publisher.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# reuse quran_source logic for caption
from quran_source import pick_surah_number, fetch_surah_meta, build_clip_plan, record_history
from video_renderer import RenderConfig, render_video, seconds_to_label
from instagram_reels_publisher import publish_reels

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--surah", type=int, default=None)
    args = parser.parse_args()

    cfg_dict = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    history_file = ROOT / cfg_dict["history_file"]
    cache_dir = ROOT / cfg_dict["cache_dir"]
    output_dir = ROOT / cfg_dict["output_dir"]

    surah_number = pick_surah_number(history_file, override=args.surah or cfg_dict.get("override_surah_number"))
    print(f"[*] السورة المختارة: {surah_number}")
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
    print(f"[*] المقطع: آية {plan[0]['number_in_surah']} إلى آية {plan[-1]['number_in_surah']} — المدة: {seconds_to_label(total_duration)}")
    render_cfg = RenderConfig(cfg_dict, ROOT)
    output_path = Path(output_dir) / f"surah_{surah_number}_{plan[0]['number_in_surah']}-{plan[-1]['number_in_surah']}.mp4"
    work_dir = ROOT / "work"
    print("[*] جاري الرندر...")
    final_path = render_video(render_cfg, surah_meta, plan, total_duration, output_path, work_dir)
    record_history(history_file, surah_meta, plan)
    print(f"[OK] الفيديو جاهز: {final_path}")

    # Build caption for Reels
    caption = f"{surah_meta['name_ar']} - الآيات {plan[0]['number_in_surah']}-{plan[-1]['number_in_surah']}\n"
    caption += f"بصوت {cfg_dict['reciter_name_ar']}\n"
    # add ayah texts short
    # full caption will be truncated by Instagram if too long, keep concise
    if len(plan) <= 3:
        for item in plan:
            caption += f"\n{item['text']}"
    print("Caption:", caption[:200])

    publish_reels(str(final_path), caption)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(1)
    except Exception as exc:
        print("\n[ERROR]", exc)
        import traceback
        traceback.print_exc()
        sys.exit(1)
