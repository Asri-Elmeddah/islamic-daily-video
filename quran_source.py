# -*- coding: utf-8 -*-
"""
quran_source.py

يتكفل بـ:
- اختيار سورة عشوائية ما تكررش (مقارنة مع data/history.json)
- جلب نص وصوت كل آية من AlQuran.cloud (edition: ar.alafasy بشكل افتراضي)
- تحميل ملفات mp3 لكل آية وحساب مدتها الحقيقية
- تحديد آخر آية "كاملة" تدخل فحدود max_video_seconds (بلا قطع)

المصادر المستعملة (موثقة، بلا تخمين):
- نص/ميتاداتا السورة:  https://api.alquran.cloud/v1/surah/{n}/quran-simple
- نص + صوت الآية:       https://api.alquran.cloud/v1/ayah/{globalAyahNumber}/{edition}
- صوت الآية (CDN مباشر): https://cdn.islamic.network/quran/audio/{bitrate}/{edition}/{globalAyahNumber}.mp3
"""

import json
import random
import re
import subprocess
import time
from pathlib import Path

import requests
from mutagen.mp3 import MP3

API_BASE = "https://api.alquran.cloud/v1"
CDN_BASE = "https://cdn.islamic.network/quran/audio"

TOTAL_SURAHS = 114

REQUEST_TIMEOUT = 45          # ثواني - مهلة أطول من قبل (كانت 20)
MAX_RETRIES = 5                # عدد المحاولات قبل ما نستسلمو
RETRY_BACKOFF_SECONDS = 4      # مدة الانتظار الأساسية بين المحاولات (تزيد تدريجيا)


def request_with_retry(method, url, **kwargs):
    """
    يدير طلب HTTP مع إعادة محاولة تلقائية عند مشاكل الشبكة
    (تايم أوت، انقطاع مؤقت، أخطاء 5xx من السيرفر). كل محاولة فاشلة
    كتستنى وقت أطول من لي قبلها (backoff).
    """
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code >= 500:
                raise requests.exceptions.HTTPError(
                    f"سيرفر رجع خطأ {response.status_code}"
                )
            return response
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.HTTPError,
        ) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS * attempt
                print(
                    f"    [!] مشكل شبكة (محاولة {attempt}/{MAX_RETRIES}): "
                    f"{exc.__class__.__name__} — نعاود بعد {wait}s..."
                )
                time.sleep(wait)
            else:
                print(f"    [x] فشلت كل المحاولات ({MAX_RETRIES}) لـ: {url}")

    raise last_error


class QuranSourceError(RuntimeError):
    pass


def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def pick_surah_number(history_file, override=None):
    """
    يختار رقم سورة عشوائي (1-114) مع تفادي آخر السور اللي
    تستعملو مؤخرا حسب history.json.
    إذا override مُعطى، يستعملو مباشرة (تحكم يدوي).
    """
    if override:
        return int(override)

    history = load_json(history_file, [])
    used_recently = {h["surah_number"] for h in history[-40:]} if history else set()

    candidates = [n for n in range(1, TOTAL_SURAHS + 1) if n not in used_recently]

    # إذا استهلكنا كامل السور فالتاريخ القريب، نرجعو نستعملو كامل اللائحة
    if not candidates:
        candidates = list(range(1, TOTAL_SURAHS + 1))

    return random.choice(candidates)


BASMALA = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"

# نفس الـ regex المستعمل في video_renderer.py لتجريد التشكيل
_ARABIC_DIACRITICS_RE = re.compile(
    r"[\u0610-\u061A\u064B-\u065F\u06D6-\u06DC\u06DF-\u06E8\u06EA-\u06ED\u08D4-\u08E1\u08E3-\u08FF]"
)

def _strip_tashkeel(t):
    return _ARABIC_DIACRITICS_RE.sub("", t)

# البسملة مجردة من التشكيل + توحيد الألف (ٱ vs ا) للمقارنة المتسامحة
_BASMALA_STRIPPED = _strip_tashkeel(BASMALA).replace("\u0671", "\u0627")


def strip_basmala_prefix(text):
    """
    كثير من السور (ما عدا التوبة) عندها نص البسملة ملصوق فبداية نص
    آية 1 من الـ API. نحيدوها من هنا باش الفيديو يبدا مباشرة بأول
    آية حقيقية ديال السورة، بلا ما نعرضو البسملة كآية.

    الإصلاح: المقارنة القديمة بـ startswith فشلت لأن ترتيب الحركات
    (شَدّة + فتحة) في ثابت BASMALA معكوس مقارنة بما يرجعه API
    (فتحة + شدة) — مثال: الله في الكود U+064E U+0651 بينما API يرجع
    U+0651 U+064E. الحل هو المقارنة بعد تجريد التشكيل وتوحيد الألف.
    ثم نحدد موضع نهاية البسملة في النص الأصلي بحساب الحروف المجردة.
    """
    # إزالة BOM ومسافات
    orig = text.strip().lstrip("\ufeff\u200e\u200f")
    # تجريد + توحيد للمقارنة (نتجاهل BOM)
    stripped_norm = _strip_tashkeel(orig).replace("\u0671", "\u0627")
    # إزالة أي BOM متبقي في stripped_norm بعد التجريد
    stripped_norm = stripped_norm.lstrip("\ufeff\u200e\u200f")
    if not stripped_norm.startswith(_BASMALA_STRIPPED):
        return text

    # إيجاد index نهاية البسملة في النص الأصلي (orig بعد تنظيف BOM)
    target = len(_BASMALA_STRIPPED)
    count = 0
    cut_idx = -1
    for i, ch in enumerate(orig):
        if not _ARABIC_DIACRITICS_RE.match(ch):
            count += 1
            if count == target:
                cut_idx = i
                break

    if cut_idx == -1:
        return text

    # ضم أي حركات متبقية مباشرة بعد آخر حرف بسملة
    while cut_idx + 1 < len(orig) and _ARABIC_DIACRITICS_RE.match(orig[cut_idx + 1]):
        cut_idx += 1

    rest = orig[cut_idx + 1 :].strip()
    # إذا كان النص هو البسملة فقط (الفاتحة آية 1) نحتفظ به كما هو
    return rest if rest else text


def fetch_surah_meta(surah_number):
    """
    يجيب ميتاداتا السورة (اسمها بالعربي، عدد آياتها، أول رقم آية عالمي).
    """
    url = f"{API_BASE}/surah/{surah_number}/quran-simple"
    response = request_with_retry("GET", url)
    response.raise_for_status()
    payload = response.json()

    if payload.get("code") != 200:
        raise QuranSourceError(f"AlQuran.cloud رجع خطأ: {payload}")

    data = payload["data"]
    ayahs = data["ayahs"]

    return {
        "number": data["number"],
        "name_ar": data["name"],  # الاسم بالخط العربي التقليدي (مثلا: سُورَةُ الفَاتِحَةِ)
        "english_name": data["englishName"],
        "ayahs_count": data["numberOfAyahs"],
        "ayahs": [
            {
                "number_in_surah": a["numberInSurah"],
                "global_number": a["number"],  # الرقم العالمي 1-6236، مطلوب لروابط الصوت
                "text": strip_basmala_prefix(a["text"]) if a["numberInSurah"] == 1 else a["text"],
            }
            for a in ayahs
        ],
    }


def download_ayah_audio(global_number, edition, bitrate, cache_dir):
    """
    يحمل ملف mp3 لآية واحدة (يستعمل الكاش إذا موجود) ويرجع المسار المحلي.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    local_path = cache_dir / f"{edition}_{bitrate}_{global_number}.mp3"

    if local_path.exists() and local_path.stat().st_size > 0:
        return local_path

    url = f"{CDN_BASE}/{bitrate}/{edition}/{global_number}.mp3"
    response = request_with_retry("GET", url)
    response.raise_for_status()

    local_path.write_bytes(response.content)
    return local_path


def get_mp3_duration_seconds(path):
    audio = MP3(str(path))
    return float(audio.info.length)


def build_clip_plan(surah_meta, edition, bitrate, cache_dir, max_seconds):
    """
    يبدا من الآية 1، يحمل صوت كل آية بالترتيب.

    المنطق الجديد (حسب طلب المستخدم):
    - نحاولو نكملو السورة كاملة إذا كانت قصيرة وتقدر تكمل فـ 60 ثانية أو أقل
      (مثلا الإخلاص، الفلق، الناس...) — فهاد الحالة نجيبو كل الآيات
      حتى لو فاتت max_seconds (30) لكن مافاتتش 60.
    - أما إذا السورة طويلة وتحتاج أكثر من 60 ثانية باش تكمل، نكتفيو
      بأول آية كاملة توصلنا أو تفوت max_seconds (30) ونوقفو بعدها.
    - بمعنى: دائما نكملو الآية الحالية (بلا قطع)، والحد الفاصل هو 30ث
      لكن نسمحو بالتمديد حتى 60ث فقط للسور القصيرة جدا.

    يرجع لائحة من عناصر:
        {
          "number_in_surah": ...,
          "global_number": ...,
          "text": ...,
          "audio_path": Path(...),
          "duration": float (ثواني),
          "start_time": float (ثواني من بداية الفيديو),
          "end_time": float,
        }
    وكذلك المدة الإجمالية الحقيقية للفيديو.
    """
    LONG_LIMIT = 60.0  # الحد الأقصى المسموح للسور القصيرة الكاملة
    # إذا max_seconds أكبر من 60 (حالة نادرة)، نخليو LONG_LIMIT هو max_seconds
    if max_seconds > LONG_LIMIT:
        LONG_LIMIT = float(max_seconds)

    plan = []
    elapsed = 0.0
    cut30_index = None  # أول آية توصلنا أو تفوت max_seconds

    for ayah in surah_meta["ayahs"]:
        audio_path = download_ayah_audio(
            ayah["global_number"], edition, bitrate, cache_dir
        )
        duration = get_mp3_duration_seconds(audio_path)

        item = {
            "number_in_surah": ayah["number_in_surah"],
            "global_number": ayah["global_number"],
            "text": ayah["text"],
            "audio_path": audio_path,
            "duration": duration,
            "start_time": elapsed,
            "end_time": elapsed + duration,
        }
        plan.append(item)
        elapsed += duration

        if cut30_index is None and elapsed >= max_seconds:
            cut30_index = len(plan)

        # إذا وصلنا أو فتنا LONG_LIMIT، نعرفو أن السورة طويلة (>60ث)
        # فنتوقفو هنا مؤقتا وسنقرر لاحقا هل نرجعو لـ cut30 أو نحتافظو بكلشي
        if elapsed >= LONG_LIMIT:
            break

    if not plan:
        raise QuranSourceError("ماقدرتش نبني أي مقطع (السورة فارغة أو فشل التحميل).")

    # هل كملنا كل آيات السورة **و** المدة الإجمالية ≤ LONG_LIMIT؟
    all_ayahs_done = len(plan) == len(surah_meta["ayahs"])

    if all_ayahs_done and elapsed <= LONG_LIMIT:
        # السورة قصيرة وكملت كلها في أقل من LONG_LIMIT (≤60ث) — نحتافظو بها كاملة
        # حتى لو فاتت max_seconds، هذا هو المطلوب
        return plan, elapsed
    else:
        # السورة طويلة: إما ما كملناش كل الآيات، أو كملناها لكن فاتت 60ث
        # نرجعو للخطة القصيرة: نقطعو عند أول آية فاتت max_seconds
        if cut30_index is not None:
            trimmed = plan[:cut30_index]
            trimmed_elapsed = trimmed[-1]["end_time"] if trimmed else 0.0
            return trimmed, trimmed_elapsed
        else:
            # حالة نادرة: ولا آية وصلت max_seconds لكن وصلنا LONG_LIMIT
            # (يعني آية واحدة طويلة >60ث) — نرجعو أول آية فقط
            return plan[:1], plan[0]["end_time"]


def record_history(history_file, surah_meta, plan):
    history = load_json(history_file, [])

    history.append(
        {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "surah_number": surah_meta["number"],
            "surah_name_ar": surah_meta["name_ar"],
            "ayah_from": plan[0]["number_in_surah"],
            "ayah_to": plan[-1]["number_in_surah"],
        }
    )

    # نخليو غير آخر 200 سجل باش الملف ما يكبرش بلا حساب
    save_json(history_file, history[-200:])
