# -*- coding: utf-8 -*-
"""
Instagram Reels Publisher for Islamic Daily Video
Publishes MP4 Reels via Instagram Graph API (Business Login)

REQUIREMENTS:
- IG_ACCESS_TOKEN : long-lived Instagram User access token (Business Login)
- IG_ACCOUNT_ID   : Instagram User ID
- CLOUDINARY_CLOUD_NAME / API_KEY / API_SECRET : for public video URL
"""

import os
import sys
import time
import hashlib
import requests

GRAPH_API_VERSION = "v21.0"
GRAPH_HOSTS = [
    f"https://graph.instagram.com/{GRAPH_API_VERSION}",
    f"https://graph.facebook.com/{GRAPH_API_VERSION}",
]

def get_env(name):
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"{name} غير موجود. set {name}=YOUR_VALUE")
    return v

def upload_video_to_catbox(video_path):
    """Fallback for video if Cloudinary fails - uses catbox.moe"""
    print("      Fallback: uploading video to catbox.moe...")
    with open(video_path, "rb") as f:
        resp = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": f},
            timeout=120,
        )
    resp.raise_for_status()
    url = resp.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"catbox video upload failed: {resp.text[:500]}")
    print(f"      catbox URL: {url}")
    try:
        h = requests.head(url, timeout=15, allow_redirects=True)
        print(f"      catbox HEAD {h.status_code} {h.headers.get('Content-Type')}")
    except Exception as e:
        print(f"      [WARN] catbox verify: {e}")
    time.sleep(2)
    return url

def upload_video_get_public_url(video_path):
    """Upload MP4 to Cloudinary (resource_type video) and return secure_url"""
    cloud_name = get_env("CLOUDINARY_CLOUD_NAME")
    api_key = get_env("CLOUDINARY_API_KEY")
    api_secret = get_env("CLOUDINARY_API_SECRET")
    timestamp = str(int(time.time()))
    to_sign = f"timestamp={timestamp}{api_secret}"
    signature = hashlib.sha1(to_sign.encode("utf-8")).hexdigest()
    print(f"      Cloud: {cloud_name}, uploading video {os.path.getsize(video_path)} bytes...")
    with open(video_path, "rb") as f:
        resp = requests.post(
            f"https://api.cloudinary.com/v1_1/{cloud_name}/video/upload",
            data={"api_key": api_key, "timestamp": timestamp, "signature": signature},
            files={"file": f},
            timeout=180,
        )
    try:
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Cloudinary video upload failed HTTP {resp.status_code}: {resp.text}") from e
    data = resp.json()
    if "secure_url" not in data:
        raise RuntimeError(f"Cloudinary video upload failed: {data}")
    url = data["secure_url"]
    print(f"      Verifying video URL...")
    try:
        head = requests.head(url, timeout=20, allow_redirects=True)
        print(f"      HEAD {head.status_code} {head.headers.get('Content-Type')} {head.headers.get('Content-Length')}")
        # Cloudinary video needs a moment to be ready for Instagram crawler
        if head.status_code == 200:
            print("      Waiting 5s for Cloudinary video to be fully ready...")
            time.sleep(5)
    except Exception as e:
        print(f"      [WARN] head verify: {e}")
        time.sleep(3)
    return url

def create_reels_container(account_id, access_token, video_url, caption):
    last_error = None
    for base in GRAPH_HOSTS:
        endpoint = f"{base}/{account_id}/media"
        payload = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": True,
            "access_token": access_token,
        }
        print(f"      Trying {base} ...")
        try:
            resp = requests.post(endpoint, data=payload, timeout=90)
            data = resp.json()
            if "id" in data:
                print(f"      Success with {base}")
                global GRAPH_API_BASE
                GRAPH_API_BASE = base
                return data["id"]
            last_error = data
            print(f"      Failed with {base}: {data}")
            err_str = str(data)
            if "2207052" in err_str or "Media download" in err_str or "9004" in err_str:
                raise RuntimeError(f"Media download failed: {data}")
        except RuntimeError:
            raise
        except Exception as e:
            last_error = str(e)
            print(f"      Exception {base}: {e}")
    raise RuntimeError(f"فشل إنشاء Reels container: {last_error}")

def wait_until_ready(container_id, access_token, timeout=300):
    base = globals().get("GRAPH_API_BASE", GRAPH_HOSTS[0])
    endpoint = f"{base}/{container_id}"
    params = {"fields": "status_code,status", "access_token": access_token}
    waited = 0
    interval = 5
    while waited < timeout:
        resp = requests.get(endpoint, params=params, timeout=30)
        data = resp.json()
        status = data.get("status_code")
        print(f"      Status poll: {status} ({waited}s) {data}")
        if status == "FINISHED":
            return True
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"فشل تجهيز الفيديو: {data}")
        time.sleep(interval)
        waited += interval
    raise RuntimeError("انتهى الوقت waiting for video")

def publish_container(account_id, access_token, container_id):
    base = globals().get("GRAPH_API_BASE", GRAPH_HOSTS[0])
    endpoint = f"{base}/{account_id}/media_publish"
    payload = {"creation_id": container_id, "access_token": access_token}
    for attempt in range(3):
        resp = requests.post(endpoint, data=payload, timeout=60)
        data = resp.json()
        if "id" in data:
            return data["id"]
        err = str(data)
        if "2207027" in err or "not ready" in err.lower() or "9007" in err:
            wait = 5*(attempt+1)
            print(f"      [RETRY] not ready {wait}s attempt {attempt+1}/3: {data}")
            time.sleep(wait)
            continue
        raise RuntimeError(f"فشل نشر الريلز: {data}")
    raise RuntimeError(f"فشل نشر الريلز بعد 3 محاولات: {data}")

def publish_reels(video_path, caption):
    access_token = get_env("IG_ACCESS_TOKEN")
    account_id = get_env("IG_ACCOUNT_ID")
    print("[1/4] رفع الفيديو لرابط عام...")
    video_url = upload_video_get_public_url(video_path)
    print("      URL:", video_url)
    try:
        print(f"      Video local size: {os.path.getsize(video_path)}")
    except Exception: pass
    print("[2/4] إنشاء Reels container...")
    try:
        container_id = create_reels_container(account_id, access_token, video_url, caption)
    except RuntimeError as e:
        err_str = str(e)
        if "2207052" in err_str or "Media download" in err_str or "9004" in err_str:
            print("      [WARN] Cloudinary video may not be fetchable, trying catbox fallback...")
            try:
                fallback_url = upload_video_to_catbox(video_path)
                print(f"      Fallback URL: {fallback_url}")
                container_id = create_reels_container(account_id, access_token, fallback_url, caption)
                print("      Fallback succeeded!")
            except Exception as fe:
                raise RuntimeError(f"Cloudinary failed: {e} | Fallback also failed: {fe}") from fe
        else:
            raise
    print("      Container ID:", container_id)
    print("[3/4] انتظار تجهيز الفيديو (قد يأخذ 30-60 ثانية)...")
    try:
        wait_until_ready(container_id, access_token)
    except RuntimeError as e:
        err_str = str(e)
        if "ERROR" in err_str and "status_code': 'ERROR'" in err_str:
            print("      [WARN] Instagram failed to process Cloudinary video, trying catbox fallback...")
            try:
                fallback_url = upload_video_to_catbox(video_path)
                print(f"      Fallback URL: {fallback_url}")
                container_id = create_reels_container(account_id, access_token, fallback_url, caption)
                print(f"      New Container ID: {container_id}")
                wait_until_ready(container_id, access_token)
            except Exception as fe:
                raise RuntimeError(f"Original error: {e} | Fallback also failed: {fe}") from fe
        else:
            raise
    time.sleep(3)
    print("[4/4] نشر الريلز...")
    media_id = publish_container(account_id, access_token, container_id)
    print("[OK] تم نشر الريلز! Media ID:", media_id)
    return media_id

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--caption", default="")
    args = p.parse_args()
    try:
        publish_reels(args.video, args.caption)
    except Exception as e:
        print("\n[ERROR]", e)
        sys.exit(1)
