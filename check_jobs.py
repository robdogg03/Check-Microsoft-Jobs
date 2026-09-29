"""Alert on every new posting at Microsoft Careers via a Telegram bot."""
import html, json, os, sys, urllib.request, urllib.parse, urllib.error

BASE = "https://apply.careers.microsoft.com"
SEARCH = BASE + "/api/pcsx/search"
STATE = "seen.json"
MAX_PAGES = 5          # newest ~50 postings per check is plenty for a 15-minute window
PAGE_SIZE = 10
MAX_LISTED = 10        # jobs listed per notification before "+N more"
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "application/json"}


def http(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers=headers or HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def notify(title, body, click=None, priority="default"):
    """Send a Telegram message. `body` is HTML (callers must escape text)."""
    if not (TG_TOKEN and TG_CHAT):
        print("Telegram secrets not set; would send:", title, body)
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
        batch = fetch_page(page * PAGE_SIZE)
        if not batch:
            break
        jobs.extend(batch)
    return jobs


def normalize(p):
    jid = str(p.get("id") or p.get("displayJobId") or "")
    locs = p.get("locations") or p.get("standardizedLocations") or []
    if isinstance(locs, list):
        locs = "; ".join(str(x) for x in locs[:3])
    url = p.get("positionUrl") or f"/careers/job/{jid}"
    if url.startswith("/"):
        url = BASE + url
    return {"id": jid, "title": p.get("name") or "Untitled", "loc": locs or "Location n/a", "url": url}


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
        # Alert on the first failure, then once a day (96 checks) if it persists
        if state["fails"] == 1 or state["fails"] % 96 == 0:
            try:
                notify("Job alert script is failing", html.escape(f"{e}\nNo alerts until it is fixed."))
            except Exception as ne:
                print("Could not send failure alert:", ne)
        save_state(state)
        return

    if state.get("fails"):
        notify("Job alert script recovered", "Checks are working again.")
    state["fails"] = 0

    first_run = not state["seen"]
    seen = set(state["seen"])
    new = [j for j in jobs if j["id"] not in seen]

    if first_run:
        print(f"First run: recording {len(new)} current postings without alerting.")
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
    state["seen"].extend(j["id"] for j in reversed(new))
    save_state(state)


if __name__ == "__main__":
    sys.exit(main())
