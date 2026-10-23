#!/usr/bin/env python3
"""Service status checker: probes every target in targets.json, writes
status.json + history.json, and (when TG env vars are present) sends
Ashkan a Telegram alert only when a target changes state."""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
TEHRAN = timezone(timedelta(hours=3, minutes=30))
UA = {"User-Agent": "AshkanStatusChecker/1.0"}


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body, int((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, int((time.time() - t0) * 1000)
    except Exception as e:
        return None, str(e), int((time.time() - t0) * 1000)


def check_target(tg):
    typ = tg["type"]
    if typ == "telegram":
        token = os.environ.get(tg["env"], "").strip()
        if not token:
            return {"ok": None, "detail": "توکن تنظیم نشده", "ms": None}
        code, body, ms = fetch(
            f"https://api.telegram.org/bot{token}/getMe", timeout=15)
        ok = code == 200 and '"ok":true' in body.replace(" ", "")
        return {"ok": ok, "detail": "پاسخ‌گو" if ok else "بدون پاسخ",
                "ms": ms}
    if typ == "http":
        code, body, ms = fetch(tg["url"])
        ok = code in tg.get("expect", [200])
        return {"ok": ok, "detail": f"کد {code}" if code else "خطای اتصال",
                "ms": ms}
    if typ == "http_any":
        code, body, ms = fetch(tg["url"])
        ok = code is not None and code < 500
        return {"ok": ok, "detail": f"کد {code}" if code else "خطای اتصال",
                "ms": ms}
    if typ == "count":
        code, body, ms = fetch(tg["url"])
        n = len([ln for ln in body.splitlines() if ln.strip()]) \
            if code == 200 else 0
        ok = code == 200 and n >= tg.get("min", 1)
        return {"ok": ok, "detail": f"{n} کانفیگ", "ms": ms}
    if typ == "freshness":
        code, body, ms = fetch(tg["url"])
        if code != 200:
            return {"ok": False, "detail": "دیتا خوانده نشد", "ms": ms}
        try:
            upd = json.loads(body)["updated_at"]
            dt = datetime.strptime(upd, "%Y-%m-%d %H:%M:%S") \
                .replace(tzinfo=TEHRAN)
            age = (datetime.now(TEHRAN) - dt).total_seconds() / 60.0
            ok = age <= tg.get("max_age_min", 120)
            return {"ok": ok,
                    "detail": f"آخرین آپدیت {int(age)} دقیقه پیش",
                    "ms": ms}
        except Exception:
            return {"ok": False, "detail": "فرمت دیتا نامشخص", "ms": ms}
    return {"ok": None, "detail": "نوع نامشخص", "ms": None}


def send_alert(text):
    token = os.environ.get("TG_ALERT_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat:
        return
    data = json.dumps({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print("alert send failed:", e, file=sys.stderr)


def main():
    with open(os.path.join(BASE, "targets.json"), encoding="utf-8") as f:
        targets = json.load(f)
    prev = {}
    sp = os.path.join(BASE, "status.json")
    if os.path.exists(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                prev = {t["id"]: t.get("ok")
                        for t in json.load(f).get("targets", [])}
        except Exception:
            prev = {}
    now = datetime.now(TEHRAN)
    results = []
    for tg in targets:
        r = check_target(tg)
        results.append({"id": tg["id"], "name": tg["name"],
                        "group": tg["group"], **r})
        print(tg["id"], r["ok"], r["detail"], flush=True)
    status = {"checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
              "checked_at_epoch": int(now.timestamp()),
              "targets": results}
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
    hp = os.path.join(BASE, "history.json")
    hist = []
    if os.path.exists(hp):
        try:
            with open(hp, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
    hist.append({"ts": status["checked_at_epoch"],
                 "states": {t["id"]: t["ok"] for t in results}})
    hist = hist[-336:]
    with open(hp, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)
    # alerts only on state change (and never on the very first run)
    if prev:
        for t in results:
            was, isnow = prev.get(t["id"]), t["ok"]
            if was is True and isnow is False:
                send_alert(f"🔴 {t['name']} از دسترس خارج شد.\n"
                           f"جزئیات: {t['detail']}")
            elif was is False and isnow is True:
                send_alert(f"🟢 {t['name']} دوباره در دسترسه.")


if __name__ == "__main__":
    main()
