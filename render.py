#!/usr/bin/env python3
"""Kindle Dash: builds a 758x1024 greyscale PNG for a Kindle Paperwhite 2.

Runs on GitHub Actions every 30 minutes. Each section fails on its own:
if a source is down, that section says so and the rest still renders.

Optional secrets (sections show "add key" without them):
  TOMTOM_KEY  - motorway + local road incidents (free, developer.tomtom.com)
  RAIL_KEY    - Cradley Heath departures (free, raildata.org.uk, Live Departure Board product)
"""
import csv, io, math, os, sys, datetime as dt
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
import requests
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- settings
LAT, LON = 52.4502, -2.0509          # Halesowen
STATION = "CRA"                      # Cradley Heath
STATION_NAME = "Cradley Heath"
NEWS_RSS = "https://www.halesowennews.co.uk/news/rss/"
NEWS_FALLBACK = "https://feeds.bbci.co.uk/news/england/birmingham_and_black_country/rss.xml"
WARNINGS_RSS = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/wm"
POWER_CSV = ("https://connecteddata.nationalgrid.co.uk/dataset/d6672e1e-c684-4cea-bb78-c7e5248b62a2/"
             "resource/a1365982-4e05-463c-8304-8323a2ba0ccd/download/live_detailed_power_cuts.csv")
POWER_RADIUS_KM = 5
MOTORWAYS = ("M5", "M6", "M42")
MOTORWAY_BBOX = "-2.35,52.30,-1.55,52.70"     # West Midlands stretches only
LOCAL_BBOX = "-2.13,52.41,-1.97,52.49"        # Halesowen and around
# Bins: Thursday collections. Food every week. This anchor Thursday is
# black (refuse) + green (garden); the following Thursday is blue (recycling).
BIN_ANCHOR = dt.date(2026, 11, 12)

W, H = 758, 1024
M = 22                    # side margin
FOOTER = 48               # bottom strip; left side kept blank for the Kindle's battery warning
TZ = ZoneInfo("Europe/London")
UA = {"User-Agent": "kindle-dash/1.0 (personal e-ink dashboard)"}
MOCK = "--mock" in sys.argv

# ---------------------------------------------------------------- fonts
def font(bold=False, size=20):
    names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    for d in ("/usr/share/fonts/truetype/dejavu/", ""):
        for n in names:
            try:
                return ImageFont.truetype(d + n, size)
            except OSError:
                pass
    return ImageFont.load_default()

F = {
    "date": font(True, 30), "tod": font(False, 24), "h": font(True, 22),
    "big": font(True, 64), "body": font(False, 20), "bodyb": font(True, 20),
    "small": font(False, 17), "pct": font(True, 16),
}

def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=25, **kw)
    r.raise_for_status()
    return r

# ---------------------------------------------------------------- data
WMO = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
       51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
       61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
       71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Light showers",
       81: "Showers", 82: "Heavy showers", 85: "Snow showers", 86: "Heavy snow showers",
       95: "Thunderstorms", 96: "Thunder and hail", 99: "Thunder and hail"}

def weather(now):
    j = get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": LAT, "longitude": LON, "timezone": "Europe/London", "forecast_days": 2,
        "wind_speed_unit": "mph",
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "hourly": "precipitation_probability,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset,wind_speed_10m_max",
    }).json()
    c, d, h = j["current"], j["daily"], j["hourly"]
    # rain timing for the rest of today
    wet = lambda i: (h["precipitation_probability"][i] or 0) >= 50 or (h["precipitation"][i] or 0) >= 0.3
    today = now.date().isoformat()
    idx = [i for i, t in enumerate(h["time"]) if t.startswith(today) and int(t[11:13]) >= now.hour]
    if not idx:
        rain = ""
    elif wet(idx[0]):
        stop = next((i for i in idx if not wet(i)), None)
        rain = f"Rain now, easing by {h['time'][stop][11:16]}" if stop else "Rain on and off for the rest of the day"
    else:
        start = next((i for i in idx if wet(i)), None)
        rain = f"Dry until {h['time'][start][11:16]}, then rain likely" if start else "Dry for the rest of the day"
    day = lambda k: {"code": WMO.get(d["weather_code"][k], "?"), "hi": round(d["temperature_2m_max"][k]),
                     "lo": round(d["temperature_2m_min"][k]), "rain": d["precipitation_probability_max"][k] or 0,
                     "wind": round(d["wind_speed_10m_max"][k])}
    return {"now_t": round(c["temperature_2m"]), "now_c": WMO.get(c["weather_code"], "?"),
            "today": day(0), "tomorrow": day(1), "rain": rain,
            "sunrise": d["sunrise"][0][11:16], "sunset": d["sunset"][0][11:16]}

def warnings():
    root = ET.fromstring(get(WARNINGS_RSS).content)
    return [i.findtext("title", "").strip() for i in root.iter("item")][:2]

def moon(now):
    days = (now.astimezone(dt.timezone.utc) - dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc)).total_seconds() / 86400
    p = (days % 29.530588) / 29.530588
    names = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
             "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"]
    return names[int((p * 8) + 0.5) % 8]

def bins(now):
    """Returns (alert, line). Alert is set on Wednesday (tomorrow) and Thursday (today)."""
    today = now.date()
    thu = today + dt.timedelta(days=(3 - today.weekday()) % 7)
    weeks = (thu - BIN_ANCHOR).days // 7
    which = "Black + Green + Food" if weeks % 2 == 0 else "Blue + Food"
    note = ""
    try:
        hols = get("https://www.gov.uk/bank-holidays.json").json()["england-and-wales"]["events"]
        monday = thu - dt.timedelta(days=3)
        for e in hols:
            if monday <= dt.date.fromisoformat(e["date"]) <= thu:
                note = f" (bank holiday week, check the date)"
    except Exception:
        pass
    if (today.month == 12 and today.day >= 18) or (today.month == 1 and today.day <= 6):
        note = " (Christmas schedule, check the date)"
    if today.weekday() == 2:
        return f"BINS OUT TONIGHT: {which}{note}", None
    if today.weekday() == 3 and now.hour < 12:
        return f"BIN DAY TODAY: {which}{note}", None
    return None, f"Next bins: Thu {thu.day} {thu:%b}, {which}{note}"

def trains():
    key = os.environ.get("RAIL_KEY")
    if not key:
        return None, ["Add RAIL_KEY secret to show departures"]
    url = f"https://api1.raildata.org.uk/1010-live-departure-board-dep/LDBWS/api/20220120/GetDepartureBoard/{STATION}"
    j = requests.get(url, headers={"x-apikey": key, **UA}, params={"numRows": 6}, timeout=25)
    j.raise_for_status()
    j = j.json()
    rows = []
    for s in (j.get("trainServices") or [])[:5]:
        dest = (s.get("destination") or [{}])[0].get("locationName", "?")
        etd = "Cancelled" if s.get("isCancelled") else s.get("etd", "")
        rows.append((s.get("std", ""), dest, etd))
    msg = (j.get("nrccMessages") or [{}])[0].get("Value", "") if j.get("nrccMessages") else ""
    import re
    msg = re.sub("<[^>]+>", "", msg).strip()
    return rows, ([msg] if msg else [])

def tomtom(bbox):
    key = os.environ.get("TOMTOM_KEY")
    if not key:
        return None
    fields = "{incidents{properties{iconCategory,magnitudeOfDelay,events{description},from,to,delay,roadNumbers}}}"
    j = get("https://api.tomtom.com/traffic/services/5/incidentDetails", params={
        "key": key, "bbox": bbox, "fields": fields, "language": "en-GB", "timeValidityFilter": "present"}).json()
    return [i["properties"] for i in j.get("incidents", [])]

def describe(p):
    road = ", ".join(r for r in (p.get("roadNumbers") or []) if not r.upper().startswith("E"))
    ev = "; ".join(e["description"] for e in (p.get("events") or []))
    where = f"{p.get('from', '')} to {p.get('to', '')}".strip(" to")
    mins = round((p.get("delay") or 0) / 60)
    delay = f" (+{mins} min)" if mins >= 1 else ""
    return f"{road} {ev}: {where}{delay}".strip()

def roads():
    mw = tomtom(MOTORWAY_BBOX)
    if mw is None:
        return ["Add TOMTOM_KEY secret to show motorways"], ["Add TOMTOM_KEY secret to show closures"]
    def is_mw(p):
        return any(r.upper().replace(" TOLL", "") in MOTORWAYS for r in (p.get("roadNumbers") or []))
    # worst first: closures (8), accidents (1), then by delay size
    rank = lambda p: (p.get("iconCategory") != 8, p.get("iconCategory") != 1, -(p.get("magnitudeOfDelay") or 0))
    mws = sorted([p for p in mw if is_mw(p) and p.get("iconCategory") in (1, 6, 7, 8, 9, 14)], key=rank)
    loc = sorted([p for p in tomtom(LOCAL_BBOX) if not is_mw(p) and p.get("iconCategory") in (1, 8)], key=rank)
    def dedupe(items):
        seen, out = set(), []
        for p in items:
            k = (frozenset((p.get("from"), p.get("to"))), p.get("iconCategory"))
            if k not in seen:
                seen.add(k); out.append(p)
        return out
    mws, loc = dedupe(mws), dedupe(loc)
    return ([describe(p) for p in mws[:3]] or ["M5 / M6 / M42: no reported problems"],
            [describe(p) for p in loc[:2]] or ["No road closures reported nearby"])

def power():
    text = get(POWER_CSV).text
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            la, lo = float(r["location_latitude"]), float(r["location_longitude"])
        except (KeyError, ValueError):
            continue
        dist = 6371 * math.acos(min(1, math.sin(math.radians(LAT)) * math.sin(math.radians(la)) +
                                    math.cos(math.radians(LAT)) * math.cos(math.radians(la)) * math.cos(math.radians(lo - LON))))
        if dist <= POWER_RADIUS_KM and not (r.get("date_of_restoration") or "").strip():
            kind = "Planned" if (r.get("planned") or "").lower() in ("true", "yes", "1") else "Power cut"
            etr = (r.get("etr") or "").strip()
            out.append(f"{kind} {r.get('postcode', '').strip()} ({dist:.1f} km)" + (f", back by {etr[11:16] or etr}" if etr else ""))
    return out[:2] or [f"No power cuts within {POWER_RADIUS_KM} km"]

def news():
    for url in (NEWS_RSS, NEWS_FALLBACK):
        try:
            root = ET.fromstring(get(url).content)
            items = [i.findtext("title", "").strip() for i in root.iter("item")]
            items = [t for t in items if t][:4]
            if items:
                return ("Halesowen News" if url == NEWS_RSS else "BBC Birmingham & Black Country"), items
        except Exception:
            continue
    raise RuntimeError("no feed")

def on_this_day(now):
    j = get(f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/selected/{now:%m}/{now:%d}").json()
    ev = j.get("selected") or []
    e = ev[now.timetuple().tm_yday % len(ev)]        # one pick per day, so it doesn't flip every refresh
    return f"{e['year']}: {e['text']}"

def time_of_day(h):
    return ("early" if h < 7 else "morning" if h < 12 else "afternoon" if h < 15
            else "late afternoon" if h < 18 else "evening")

# ---------------------------------------------------------------- drawing
class Page:
    def __init__(self):
        self.img = Image.new("L", (W, H), 255)
        self.d = ImageDraw.Draw(self.img)
        self.y = 0
        self.limit = H - FOOTER

    def wrap(self, text, f, width, max_lines):
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if self.d.textlength(t, font=f) <= width:
                cur = t
            else:
                lines.append(cur); cur = w
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            while self.d.textlength(lines[-1] + "...", font=f) > width:
                lines[-1] = lines[-1][:-1]
            lines[-1] += "..."
        return lines

    def text(self, s, f="body", x=M, max_lines=1, gap=4, fill=0, width=None):
        width = width or W - x - M
        for line in self.wrap(s, F[f], width, max_lines):
            h = F[f].getbbox("Ag")[3]
            if self.y + h > self.limit:
                return
            self.d.text((x, self.y), line, font=F[f], fill=fill)
            self.y += h + gap

    def heading(self, s):
        self.y += 6
        if self.y + 30 > self.limit:
            self.y = self.limit; return
        self.d.text((M, self.y), s.upper(), font=F["h"], fill=0)
        self.y += 28
        self.d.line((M, self.y, W - M, self.y), fill=0, width=2)
        self.y += 7

    def alert(self, s):
        lines = self.wrap(s, F["bodyb"], W - 2 * M - 24, 2)
        h = len(lines) * 26 + 10
        self.d.rectangle((M, self.y, W - M, self.y + h), fill=0)
        yy = self.y + 5
        for l in lines:
            self.d.text((M + 12, yy), l, font=F["bodyb"], fill=255); yy += 26
        self.y += h + 6

def section(fn, *a):
    if MOCK:
        return MOCK_DATA[fn.__name__]
    try:
        return fn(*a)
    except Exception as e:
        print(f"{fn.__name__} failed: {e}", file=sys.stderr)
        return None

def build(now):
    p = Page()
    d = p.d
    # header: date, time of day, day progress
    p.y = 14
    d.text((M, p.y), f"{now:%A} {now.day} {now:%B}".upper(), font=F["date"], fill=0)
    tod = time_of_day(now.hour)
    d.text((W - M - d.textlength(tod, font=F["tod"]), p.y + 6), tod, font=F["tod"], fill=0)
    p.y += 46
    pct = (now.hour * 60 + now.minute) / 1440
    d.rectangle((M, p.y, W - M, p.y + 22), outline=0, width=2)
    d.rectangle((M, p.y, M + int((W - 2 * M) * pct), p.y + 22), fill=0)
    label = f"{round(pct * 100)}%"
    lx = M + int((W - 2 * M) * pct) + 6
    if lx + 50 > W - M:
        d.text((lx - 56, p.y + 2), label, font=F["pct"], fill=255)
    else:
        d.text((lx, p.y + 2), label, font=F["pct"], fill=0)
    p.y += 32

    # warnings + bins alert go to the top when they apply
    for wtxt in section(warnings) or []:
        p.alert(wtxt)
    alert, binline = bins(now) if not MOCK else MOCK_DATA["bins"]
    if alert:
        p.alert(alert)

    # weather
    w = section(weather, now)
    p.heading("Weather")
    if w:
        top = p.y
        t = f"{w['now_t']}°"
        d.text((M, top - 6), t, font=F["big"], fill=0)
        x = M + d.textlength(t, font=F["big"]) + 22
        p.y = top
        p.text(f"Now: {w['now_c']}", "bodyb", x=x)
        tw = w["today"]
        p.text(f"Today {tw['hi']}° / {tw['lo']}°, rain {tw['rain']}%, wind {tw['wind']} mph", x=x)
        p.text(w["rain"], x=x)
        p.y = max(p.y, top + 72)
        tm = w["tomorrow"]
        p.text(f"Tomorrow: {tm['code']}, {tm['hi']}° / {tm['lo']}°, rain {tm['rain']}%, wind {tm['wind']} mph")
        p.text(f"Sunrise {w['sunrise']}  |  Sunset {w['sunset']}  |  {moon(now)}", "small", fill=60)
    else:
        p.text("Weather unavailable")
    if binline:
        p.text(binline, "small", fill=60)

    # trains
    p.heading(f"Trains from {STATION_NAME}")
    tr = section(trains)
    if tr is None:
        p.text("Departures unavailable")
    else:
        rows, msgs = tr
        for std, dest, etd in rows or []:
            y = p.y
            d.text((M, y), std, font=F["bodyb"], fill=0)
            status_f = F["bodyb"] if etd not in ("On time",) else F["body"]
            sw = d.textlength(etd, font=status_f)
            d.text((W - M - sw, y), etd, font=status_f, fill=0)
            dl = p.wrap(dest, F["body"], W - 2 * M - 80 - sw - 12, 1)[0]
            d.text((M + 80, y), dl, font=F["body"], fill=0)
            p.y += 26
        if rows == []:
            p.text("No departures listed")
        for m in msgs:
            p.text(m, "small", max_lines=2, fill=60)

    # roads
    r = section(roads)
    p.heading("Roads")
    if r:
        for s in r[0]:
            p.text(s)
        p.y += 4
        for s in r[1]:
            p.text(s, max_lines=2)
    else:
        p.text("Traffic unavailable")

    # power
    pw = section(power)
    p.heading("Power")
    for s in pw or ["Power cut data unavailable"]:
        p.text(s)

    # news
    n = section(news)
    p.heading(n[0] if n else "News")
    for s in (n[1] if n else ["News unavailable"]):
        p.text(s)

    # on this day
    o = section(on_this_day, now)
    p.heading("On this day")
    p.text(o or "Unavailable", max_lines=3)

    # footer: left blank for the Kindle's own battery warning
    upd = f"Updated {now:%H:%M}"
    d.text((W - M - d.textlength(upd, font=F["small"]), H - 28), upd, font=F["small"], fill=0)
    return p.img

MOCK_DATA = {
    "warnings": ["Yellow warning of rain affecting West Midlands"],
    "bins": ("BINS OUT TONIGHT: Black + Green + Food", None),
    "weather": {"now_t": 9, "now_c": "Partly cloudy", "rain": "Dry until 15:00, then rain likely",
                "today": {"code": "Showers", "hi": 11, "lo": 5, "rain": 70, "wind": 18},
                "tomorrow": {"code": "Light rain", "hi": 10, "lo": 4, "rain": 60, "wind": 14},
                "sunrise": "07:12", "sunset": "16:22"},
    "trains": ([("07:41", "Birmingham Snow Hill", "On time"), ("07:52", "Kidderminster", "07:58"),
                ("08:11", "Stratford-upon-Avon", "On time"), ("08:22", "Worcester Foregate Street", "Cancelled"),
                ("08:41", "Birmingham Snow Hill", "On time")], []),
    "roads": (["M5 Queuing traffic: J3 Quinton to J2 Oldbury (+12 min)", "M6 Lane closed: J7 to J6 Spaghetti Junction"],
              ["A458 Road closed: Mucklow Hill to Long Lane"]),
    "power": ["No power cuts within 5 km"],
    "news": ("Halesowen News", ["Plans lodged for new homes on former factory site in Halesowen",
                                "Bonfire night display draws record crowd to Leasowes Park",
                                "Roadworks on Hagley Road set to finish next week",
                                "Local school celebrates outstanding Ofsted report"]),
    "on_this_day": "1895: Wilhelm Röntgen discovers X-rays while experimenting with cathode-ray tubes.",
}

if __name__ == "__main__":
    now = dt.datetime.now(TZ)
    if MOCK:
        now = now.replace(year=2026, month=11, day=11, hour=7, minute=30)
    img = build(now)
    os.makedirs("out", exist_ok=True)
    img.save("out/dash.png", optimize=True)
    print("wrote out/dash.png")
