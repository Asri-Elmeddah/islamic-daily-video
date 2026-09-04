# -*- coding: utf-8 -*-
"""
video_renderer.py - التصميم المطابق تماما للصورة المرجعية (شريط واحد فقط)

- خلفية ضبابية (blur) لنفس صورة assets/background.png
- كارت زجاجي (frosted glass) أبيض شفاف بأركان مدورة في الوسط
- صورة داخلية واضحة (cover) بأركان مدورة داخل الكارت
- نص الآيات (ايات السورة) في وسط الصورة الداخلية بخط أبيض مع stroke أسود
- تحت الصورة: اسم السورة (كبير بني) ثابت + اسم القارئ (صغير بني) ثابت تحته — كيما في الصورة تماما (بدون تكرار / بدون سكرول)
- شريط تقدم واحد فقط يتحرك مع زمن الفيديو + عدّاد 00:00 على اليسار يزيد و المدة الكاملة على اليمين
- أزرار تحكم شكلية بنية في الوسط
"""

import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import re
import arabic_reshaper

# ============================================================
# رسم النص العربي
# ============================================================
ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u06D6-\u06DC\u06DF-\u06E8\u06EA-\u06ED\u08D4-\u08E1\u08E3-\u08FF]")


def strip_tashkeel(text):
    return ARABIC_DIACRITICS.sub("", text)


def reshape_text(text):
    return arabic_reshaper.reshape(strip_tashkeel(text))


def basic_font(path, size):
    return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)


def rtl_text_width(draw, text, fnt):
    reshaped = reshape_text(text)
    return sum(draw.textlength(ch, font=fnt) for ch in reshaped)


def draw_rtl_text(draw, text, right_x, top_y, fnt, fill, stroke_width=0, stroke_fill=None):
    reshaped = reshape_text(text)
    x = right_x
    positions = []
    for ch in reshaped:
        w = draw.textlength(ch, font=fnt)
        x -= w
        positions.append((x, ch))
    if stroke_width > 0 and stroke_fill:
        for px, ch in positions:
            for dx in range(-stroke_width, stroke_width + 1):
                for dy in range(-stroke_width, stroke_width + 1):
                    if dx == 0 and dy == 0:
                        continue
                    draw.text((px + dx, top_y + dy), ch, font=fnt, fill=stroke_fill)
    for px, ch in positions:
        draw.text((px, top_y), ch, font=fnt, fill=fill)
    return right_x - x


def text_size(draw, text, fnt):
    w = rtl_text_width(draw, text, fnt)
    box = fnt.getbbox("أبجدية")
    h = box[3] - box[1]
    return w, h


def wrap_arabic(text, max_chars=26, max_lines=6):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        if len(trial) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines[:max_lines]


# ============================================================
# CONFIG / COLORS - مطابقة للصورة
# ============================================================

class RenderConfig:
    def __init__(self, cfg_dict, root):
        self.width = cfg_dict["video_width"]
        self.height = cfg_dict["video_height"]
        self.fps = cfg_dict["fps"]
        self.background_path = root / cfg_dict["background_image"]
        # خلفية الآيات فقط (الصورة اللي فوق) - إذا مش موجودة نستعمل نفس الخلفية الكاملة
        ayah_bg = cfg_dict.get("ayah_background", cfg_dict["background_image"])
        self.ayah_background_path = root / ayah_bg
        self.font_regular_path = root / cfg_dict["font_regular"]
        self.font_bold_path = root / cfg_dict["font_bold"]
        self.reciter_name = cfg_dict["reciter_name_ar"]


# كيما في الصورة: كارت زجاجي شفاف جدا + بني للشريط والنصوص
CARD_FILL = (250, 248, 242, 135)        # شفاف بزاف كيما الصورة (أبيض زجاجي)
CARD_BORDER = (255, 255, 255, 80)
TEXT_WHITE = (255, 255, 255, 255)
TEXT_WHITE_STROKE = (0, 0, 0, 210)
SURAH_COLOR = (74, 44, 17, 255)        # بني غامق لاسم السورة
RECITER_COLOR = (92, 70, 46, 255)      # بني أفتح لاسم القارئ
TIME_COLOR = (88, 66, 44, 255)
PROGRESS_BG = (188, 178, 164, 255)     # خلفية الشريط (بيج)
PROGRESS_FG = (98, 70, 42, 255)        # لون التقدم والـ thumb
ICON_COLOR = (110, 88, 66, 255)        # لون الأيقونات كيما الصورة (فاتح شوية)
ICON_COLOR_MUTED = (150, 135, 115, 210)


def font(path, size):
    return basic_font(path, size)


def marquee_offset(elapsed_seconds, text_width, box_width, speed_px_per_sec=50, gap=90):
    """سكرول اسم السورة فقط لليمين حتى نهاية الإطار الأبيض"""
    cycle_length = text_width + gap
    if cycle_length <= 0:
        return 0
    progress_px = (elapsed_seconds * speed_px_per_sec) % cycle_length
    start_x = -text_width + progress_px
    return start_x


# ============================================================
# HELPERS
# ============================================================

def build_blurred_background(src_image, width, height, blur_radius=30):
    bg = src_image.resize((width, height), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 22))
    bg = bg.convert("RGBA")
    bg = Image.alpha_composite(bg, overlay)
    return bg


def crop_white_border(src_image, threshold=245):
    """ينزع الخلفية البيضاء المحيطة بالصورة (كيما الصورة اللي بعتها) ويرجع الصورة مقصوصة بدون الأبيض"""
    # نحول لصورة RGB ونقلب على الباوندينغ بوكس للبيكسلات غير بيضاء
    if src_image.mode != "RGB":
        src_image = src_image.convert("RGB")
    w, h = src_image.size
    # نلقاو حدود المحتوى غير الأبيض
    left, top, right, bottom = w, h, 0, 0
    # مسح سريع بخطوة 2px باش يكون أسرع
    pixels = src_image.load()
    found = False
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = pixels[x, y]
            if r < threshold or g < threshold or b < threshold:
                if x < left:
                    left = x
                if y < top:
                    top = y
                if x > right:
                    right = x
                if y > bottom:
                    bottom = y
                found = True
    if not found:
        return src_image
    pad = 4
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(w, right + pad + 1)
    bottom = min(h, bottom + pad + 1)
    # إذا المحتوى تقريبا كامل الصورة (مافيهاش خلفية بيضاء كبيرة) نرجع الأصلية بدون قص
    if (right - left) > w * 0.90 and (bottom - top) > h * 0.90:
        return src_image
    return src_image.crop((left, top, right, bottom))


def build_inner_image(src_image, box_w, box_h, radius=26):
    # قبل القص للـ cover، ننزع الخلفية البيضاء إذا موجودة (للصورة الجديدة اللي بعتها)
    src_image = crop_white_border(src_image)
    src_w, src_h = src_image.size
    scale = max(box_w / src_w, box_h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    resized = src_image.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - box_w) // 2
    top = (new_h - box_h) // 2
    top = max(0, top - int(new_h * 0.06))
    cropped = resized.crop((left, top, left + box_w, top + box_h))
    mask = Image.new("L", (box_w, box_h), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle([0, 0, box_w, box_h], radius=radius, fill=255)
    rounded = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    rounded.paste(cropped.convert("RGBA"), (0, 0), mask)
    return rounded


# ============================================================
# FRAME BUILDING - مطابق للصورة 100%
# ============================================================

def render_frame(cfg, background_blurred, inner_image_prototype, fonts,
                 ayah_text, surah_name_ar, reciter_name,
                 elapsed, total_duration, elapsed_label, total_label, text_alpha=1.0):
    width, height = cfg.width, cfg.height
    frame = background_blurred.copy()
    draw = ImageDraw.Draw(frame, "RGBA")

    # --- الكارت الزجاجي ---
    card_left = int(width * 0.065)
    card_right = width - card_left
    card_top = int(height * 0.068)
    card_bottom = int(height * 0.932)
    card_radius = 56

    card_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(card_layer, "RGBA")
    # ظل خفيف
    cdraw.rounded_rectangle(
        [card_left + 3, card_top + 6, card_right + 3, card_bottom + 6],
        radius=card_radius, fill=(0, 0, 0, 26)
    )
    cdraw.rounded_rectangle(
        [card_left, card_top, card_right, card_bottom],
        radius=card_radius, fill=CARD_FILL
    )
    cdraw.rounded_rectangle(
        [card_left, card_top, card_right, card_bottom],
        radius=card_radius, outline=CARD_BORDER, width=1
    )
    frame = Image.alpha_composite(frame, card_layer)
    draw = ImageDraw.Draw(frame, "RGBA")

    # --- الصورة الداخلية ---
    inner_pad = 24
    img_left = card_left + inner_pad
    img_right = card_right - inner_pad
    img_top = card_top + inner_pad
    img_width = img_right - img_left
    img_height = int(img_width * 0.94)
    card_h = card_bottom - card_top
    max_h = int(card_h * 0.58)
    if img_height > max_h:
        img_height = max_h
    img_bottom = img_top + img_height

    frame.paste(inner_image_prototype, (img_left, img_top), inner_image_prototype)

    # --- نص الآيات في وسط الصورة ---
    lines = wrap_arabic(ayah_text, max_chars=24, max_lines=6)
    text_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(text_layer, "RGBA")
    img_center_y = img_top + img_height // 2
    sizes = [text_size(tdraw, x, fonts["ayah"]) for x in lines]
    heights = [h for _, h in sizes]
    total_h = sum(heights) + 18 * max(0, len(lines) - 1)
    y = img_center_y - total_h // 2
    for line, (w, h) in zip(lines, sizes):
        right_x = img_left + (img_width + w) // 2
        draw_rtl_text(tdraw, line, right_x, y, fonts["ayah"], TEXT_WHITE,
                      stroke_width=3, stroke_fill=TEXT_WHITE_STROKE)
        y += h + 18
    if text_alpha < 1.0:
        alpha_channel = text_layer.getchannel("A").point(
            lambda a: int(a * max(0.0, min(1.0, text_alpha)))
        )
        text_layer.putalpha(alpha_channel)
    frame = Image.alpha_composite(frame, text_layer)
    draw = ImageDraw.Draw(frame, "RGBA")

    # --- اسم السورة واسم القارئ ---
    outer_pad = 30
    content_left = card_left + outer_pad
    content_right = card_right - outer_pad
    content_width = content_right - content_left

    # اسم السورة: مكبّر قليلا + نازل شوية + ينزاح لليمين حتى نهاية الإطار الأبيض
    surah_y = img_bottom + 72
    s_w, s_h = text_size(draw, surah_name_ar, fonts["surah"])
    # صندوق السكرول = عرض الكارت الأبيض الداخلي
    marquee_box_left = content_left
    marquee_box_width = content_width
    marquee_box_h = s_h + 14
    offset_x = marquee_offset(elapsed, s_w, marquee_box_width, speed_px_per_sec=48, gap=100)
    marquee_layer = Image.new("RGBA", (marquee_box_width, marquee_box_h), (0, 0, 0, 0))
    mdraw = ImageDraw.Draw(marquee_layer, "RGBA")
    draw_rtl_text(mdraw, surah_name_ar, offset_x + s_w, 4, fonts["surah"], SURAH_COLOR)
    frame.paste(marquee_layer, (marquee_box_left, surah_y), marquee_layer)

    # اسم القارئ: مكبّر قليلا + نازل مع السورة لكن ثابت (ما ينزاحش)
    reciter_y = surah_y + marquee_box_h + 8
    r_w, r_h = text_size(draw, reciter_name, fonts["reciter"])
    draw_rtl_text(draw, reciter_name, content_left + r_w, reciter_y, fonts["reciter"], RECITER_COLOR)

    # --- شريط واحد فقط + التوقيت (00:00 على اليسار، المدة الكاملة على اليمين) ---
    # حسب طلب المستخدم الأخير: نرفعه قليلا حوالي ثمن المسافة للأعلى (من 210 إلى 300px فوق الحافة)
    bar_left = content_left
    bar_right = content_right
    bar_width = bar_right - bar_left
    bar_h = 6
    bar_top_y = card_bottom - 300
    progress_ratio = 0 if total_duration <= 0 else min(1.0, elapsed / total_duration)
    # خلفية الشريط
    draw.rounded_rectangle(
        [bar_left, bar_top_y, bar_right, bar_top_y + bar_h],
        radius=bar_h // 2, fill=PROGRESS_BG
    )
    filled_right = bar_left + int(bar_width * progress_ratio)
    if filled_right > bar_left:
        draw.rounded_rectangle(
            [bar_left, bar_top_y, filled_right, bar_top_y + bar_h],
            radius=bar_h // 2, fill=PROGRESS_FG
        )
        # Thumb
        thumb_r = 10
        thumb_cx = min(filled_right, bar_right - 2)
        if thumb_cx < bar_left + thumb_r:
            thumb_cx = bar_left + thumb_r
        thumb_cy = bar_top_y + bar_h // 2
        draw.ellipse(
            [thumb_cx - thumb_r, thumb_cy - thumb_r, thumb_cx + thumb_r, thumb_cy + thumb_r],
            fill=PROGRESS_FG
        )

    # التوقيت: تحت الشريط مباشرة (على يسار 00:00 وعلى يمين المدة الكاملة)
    time_y = bar_top_y + 18
    time_font = fonts["time"]
    draw.text((content_left, time_y), elapsed_label, font=time_font, fill=TIME_COLOR)
    total_w = draw.textlength(total_label, font=time_font)
    draw.text((content_right - total_w, time_y), total_label, font=time_font, fill=TIME_COLOR)

    # --- أزرار التحكم (شكلية فقط) — فوق الشريط قليلا كي يبقى في الربع الأخير ---
    controls_y = bar_top_y - 68
    center_x = width // 2
    draw_playback_icons(draw, center_x, controls_y)

    # ملاحظة: لا يوجد شريط صوت ثاني - شريط واحد فقط حسب الطلب
    return frame


def draw_playback_icons(draw, center_x, y):
    """أزرار: كلها في اتجاه واحد لليمين حسب طلب المستخدم"""
    # الوسط: مثلث Play كبير يشير لليمين
    play_w, play_h = 38, 48
    draw.polygon(
        [
            (center_x - play_w//2 + 4, y - play_h//2),
            (center_x - play_w//2 + 4, y + play_h//2),
            (center_x + play_w//2 + 4, y),
        ],
        fill=ICON_COLOR
    )
    # السابق (يسار) — مثلث يشير لليمين
    bwd_cx = center_x - 155
    tri_w, tri_h = 24, 30
    draw.rectangle([bwd_cx - 20, y - tri_h//2, bwd_cx - 16, y + tri_h//2], fill=ICON_COLOR_MUTED)
    draw.polygon(
        [
            (bwd_cx - tri_w//2, y - tri_h//2),
            (bwd_cx - tri_w//2, y + tri_h//2),
            (bwd_cx + tri_w//2, y),
        ],
        fill=ICON_COLOR_MUTED
    )
    # التالي (يمين) — تم تصحيحه ليشير لليمين كيما باقي المثلثات (كان يشير لليسار قبل)
    fwd_cx = center_x + 155
    draw.rectangle([fwd_cx + 16, y - tri_h//2, fwd_cx + 20, y + tri_h//2], fill=ICON_COLOR_MUTED)
    draw.polygon(
        [
            (fwd_cx - tri_w//2, y - tri_h//2),
            (fwd_cx - tri_w//2, y + tri_h//2),
            (fwd_cx + tri_w//2, y),
        ],
        fill=ICON_COLOR_MUTED
    )


def seconds_to_label(seconds):
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def split_ayah_into_halves(ayah_text):
    words = ayah_text.split()
    if len(words) <= 3:
        return [ayah_text]
    mid = math.ceil(len(words) / 2)
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def get_current_ayah_segment(ayah, elapsed):
    segments = split_ayah_into_halves(ayah["text"])
    if len(segments) == 1:
        return segments[0], ayah["start_time"], ayah["end_time"]
    half_duration = ayah["duration"] / 2
    mid_time = ayah["start_time"] + half_duration
    ayah_progress = elapsed - ayah["start_time"]
    if ayah_progress < half_duration:
        return segments[0], ayah["start_time"], mid_time
    return segments[1], mid_time, ayah["end_time"]


def fade_alpha(elapsed, segment_start, segment_end, fade_seconds=0.35):
    duration = segment_end - segment_start
    fade = min(fade_seconds, duration / 2) if duration > 0 else 0
    if fade <= 0:
        return 1.0
    time_in = elapsed - segment_start
    time_to_end = segment_end - elapsed
    if time_in < fade:
        return max(0.0, min(1.0, time_in / fade))
    if time_to_end < fade:
        return max(0.0, min(1.0, time_to_end / fade))
    return 1.0


# ============================================================
# MAIN RENDER PIPELINE
# ============================================================

def render_video(cfg, surah_meta, plan, total_duration, output_path, work_dir):
    work_dir = Path(work_dir)
    frames_dir = work_dir / "frames"
    if frames_dir.exists():
        for old_frame in frames_dir.glob("frame_*.jpg"):
            try:
                old_frame.unlink()
            except Exception:
                pass
        for old_frame in frames_dir.glob("frame_*.png"):
            try:
                old_frame.unlink()
            except Exception:
                pass
    frames_dir.mkdir(parents=True, exist_ok=True)

    src_original = Image.open(cfg.background_path).convert("RGB")
    background_blurred = build_blurred_background(src_original, cfg.width, cfg.height, blur_radius=30)
    # صورة الآيات فقط (منفصلة عن الخلفية الكاملة) - ننزع الخلفية البيضاء إذا فيها
    try:
        src_ayah = Image.open(cfg.ayah_background_path).convert("RGB")
    except Exception:
        src_ayah = src_original

    tmp_card_left = int(cfg.width * 0.065)
    tmp_card_right = cfg.width - tmp_card_left
    tmp_inner_pad = 24
    tmp_img_left = tmp_card_left + tmp_inner_pad
    tmp_img_right = tmp_card_right - tmp_inner_pad
    tmp_img_width = tmp_img_right - tmp_img_left
    tmp_img_height = int(tmp_img_width * 0.94)
    tmp_card_h = int(cfg.height * 0.932) - int(cfg.height * 0.068)
    max_h = int(tmp_card_h * 0.58)
    if tmp_img_height > max_h:
        tmp_img_height = max_h

    inner_image_prototype = build_inner_image(src_ayah, tmp_img_width, tmp_img_height, radius=26)

    fonts = {
        "ayah": font(cfg.font_regular_path, 52),
        "surah": font(cfg.font_bold_path, 46),
        "reciter": font(cfg.font_regular_path, 30),
        "time": font(cfg.font_regular_path, 20),
    }

    surah_name_ar = surah_meta["name_ar"]
    reciter_name = cfg.reciter_name
    total_label = seconds_to_label(total_duration)

    total_frames = int(math.ceil(total_duration * cfg.fps))

    for frame_index in range(total_frames):
        elapsed = frame_index / cfg.fps
        current_ayah = next(
            (a for a in plan if a["start_time"] <= elapsed < a["end_time"]),
            plan[-1],
        )
        ayah_segment_text, segment_start, segment_end = get_current_ayah_segment(current_ayah, elapsed)
        text_alpha = fade_alpha(elapsed, segment_start, segment_end)
        elapsed_label = seconds_to_label(elapsed)
        frame = render_frame(
            cfg, background_blurred, inner_image_prototype, fonts,
            ayah_text=ayah_segment_text,
            surah_name_ar=surah_name_ar,
            reciter_name=reciter_name,
            elapsed=elapsed,
            total_duration=total_duration,
            elapsed_label=elapsed_label,
            total_label=total_label,
            text_alpha=text_alpha,
        )
        frame.convert("RGB").save(
            frames_dir / f"frame_{frame_index:06d}.jpg",
            quality=92,
        )

    concat_list_path = work_dir / "audio_concat.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for item in plan:
            f.write(f"file '{item['audio_path'].resolve()}'\n")

    combined_audio_path = work_dir / "combined_audio.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list_path), "-c", "copy", str(combined_audio_path)],
        check=True, capture_output=True,
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(cfg.fps), "-i", str(frames_dir / "frame_%06d.jpg"), "-i", str(combined_audio_path),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", str(output_path)],
        check=True, capture_output=True,
    )
    return output_path
