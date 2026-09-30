"""Alert on every new posting at Microsoft Careers via a Telegram bot."""
import html, json, os, sys, time, urllib.request, urllib.parse, urllib.error

BASE = "https://apply.careers.microsoft.com"
SEARCH = BASE + "/api/pcsx/search"
STATE = "seen.json"
MAX_PAGES = 2          # newest ~20 postings per check is plenty for a 15-minute window
PAGE_SIZE = 10
MAX_LISTED = 10        # jobs listed per notification before "+N more"
# Only alert on postings whose location mentions one of these (case-insensitive).
# Postings with no location info are kept so nothing is missed by accident.
LOCATION_KEYWORDS = ["united states", "usa", "multiple locations"]
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "application/json"}


def http(url, data=None, headers=None, timeout=30, attempts=3):
    """GET/POST with retries on rate limits (429) and server errors (5xx)."""
    for i in range(attempts):
        req = urllib.request.Request(url, data=data, headers=headers or HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < attempts - 1:
                wait = min(int(e.headers.get("Retry-After", 0) or 0) or 15 * (i + 1), 60)
                print(f"HTTP {e.code}; retrying in {wait}s")
                time.sleep(wait)
                continue
            raise


def notify(title, body, click=None, priority="default"):
    """Send a Telegram message. `body` is HTML (callers must escape text)."""
    if not (TG_TOKEN and TG_CHAT):
        print("Telegram secrets not set; would send:", title, body)
        return
    if not (TG_TOKEN.isascii() and TG_CHAT.isascii()):
        print("TELEGRAM secret contains a non-standard character (often a slashed zero "
              "typed instead of 0). Re-copy the token/chat ID and re-save the secret.")
        return
    text = f"<b>{html.escape(title)}</b>\n{body}"
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    http(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data,
         headers={"Content-Type": "application/x-www-form-urlencoded"})


def fetch_page(start):
    q = urllib.parse.urlencode({"domain": "microsoft.com", "query": "", "location": "",
                                "start": start, "sort_by": "timestamp"})
    payload = json.loads(http(f"{SEARCH}?{q}"))
    data = payload.get("data", payload)
    return data.get("positions") or []


def fetch_recent():
    jobs = []
    for page in range(MAX_PAGES):
        if page:
            time.sleep(3)
        batch = fetch_page(page * PAGE_SIZE)
        if not batch:
            break
        jobs.extend(batch)
    return jobs


def normalize(p):
    jid = str(p.get("id") or p.get("displayJobId") or "")
    locs = p.get("locations") or p.get("standardizedLocations") or []
    if isinstance(locs, list):
        full = "; ".join(str(x) for x in locs)
        locs = "; ".join(str(x) for x in locs[:3])
    else:
        full = str(locs)
    url = p.get("positionUrl") or f"/careers/job/{jid}"
    if url.startswith("/"):
        url = BASE + url
    return {"id": jid, "title": p.get("name") or "Untitled", "loc": locs or "Location n/a",
            "full_loc": full, "url": url}


def wanted(job):
    text = job["full_loc"].lower()
    if not text.strip():
        return True
    return any(k in text for k in LOCATION_KEYWORDS)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"seen": [], "fails": 0}


def save_state(s):
    s["seen"] = s["seen"][-5000:]
    with open(STATE, "w") as f:
        json.dump(s, f)


def main():
    state = load_state()
    try:
        jobs = [normalize(p) for p in fetch_recent()]
        jobs = [j for j in jobs if j["id"]]
        if not jobs:
            raise RuntimeError("Feed returned zero postings (endpoint may have changed)")
    except Exception as e:
        state["fails"] = state.get("fails", 0) + 1
        print("FAILED:", e)
        # One-off errors (like a rate limit) are ignored; alert after 3 in a row (~45 min),
        # then once a day if it persists
        if state["fails"] == 3 or state["fails"] % 96 == 0:
            try:
                notify("Job alert script is failing", html.escape(f"{e}\nNo alerts until it is fixed."))
            except Exception as ne:
                print("Could not send failure alert:", ne)
        save_state(state)
        return

    if state.get("fails", 0) >= 3:
        notify("Job alert script recovered", "Checks are working again.")
    state["fails"] = 0

    first_run = not state["seen"]
    seen = set(state["seen"])
    all_new = [j for j in jobs if j["id"] not in seen]
    new = [j for j in all_new if wanted(j)]
    print(f"{len(all_new)} new postings, {len(new)} match the location filter.")

    if first_run:
        print(f"First run: recording {len(all_new)} current postings without alerting.")
        notify("Microsoft job alerts are live", "Setup works. You'll be notified of every new posting from now on.")
    elif new:
        lines = [f"• <a href=\"{html.escape(j['url'], quote=True)}\">{html.escape(j['title'])}</a>"
                 f" ({html.escape(j['loc'])})" for j in new[:MAX_LISTED]]
        if len(new) > MAX_LISTED:
            lines.append(f"+{len(new) - MAX_LISTED} more")
        title = f"{len(new)} new Microsoft job{'s' if len(new) != 1 else ''}"
        notify(title, "\n".join(lines))
        print(f"Alerted on {len(new)} new postings.")
    else:
        print("No new postings.")

    # oldest first so trimming keeps the newest IDs
    state["seen"].extend(j["id"] for j in reversed(all_new))
    save_state(state)


if __name__ == "__main__":
    sys.exit(main())
