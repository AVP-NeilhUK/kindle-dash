#!/usr/bin/env python3
"""Kindle Dash: builds a 758x1024 greyscale PNG for a Kindle Paperwhite 2.

Runs on GitHub Actions every 30 minutes. Each section fails on its own:
if a source is down, that section says so and the rest still renders.

Optional secrets (sections show a note without them):
  TOMTOM_KEY  - motorway + local road incidents (free, developer.tomtom.com)
  RAIL_KEY    - Cradley Heath departures (free, raildata.org.uk, Live Departure Board product)
"""
import csv, io, json, math, os, re, sys, datetime as dt
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
import requests
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFont, ImageOps

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
FUEL_RADIUS_KM = 5
MOTORWAYS = ("M5", "M6", "M42")
MOTORWAY_BBOX = "-2.35,52.30,-1.55,52.70"     # West Midlands stretches only
LOCAL_BBOX = "-2.13,52.41,-1.97,52.49"        # Halesowen and around
# Bins: Thursday collections. Food every week. This anchor Thursday is
# black (refuse) + green (garden); the following Thursday is blue (recycling).
BIN_ANCHOR = dt.date(2026, 11, 12)

# Front light (Paperwhite 2: 0 = off, 24 = full), following daylight.
FRONTLIGHT = {"night": 0, "dawn": 12, "twilight": 8, "day": 0, "evening": 10}
NIGHT_FROM, NIGHT_UNTIL = 23, 6       # hours: light off overnight

# Painting of the day, faded behind the dashboard (Cleveland Museum of Art, public domain).
ART_QUERY = "landscape"        # what to search the Cleveland Museum's paintings for
ART_STRENGTH = 0.32             # 0 = invisible, 1 = full strength

W, H = 758, 1024
M = 26                    # side margin
FOOTER = 70               # bottom strip: painting note, power status, update time
TZ = ZoneInfo("Europe/London")
UA = {"User-Agent": "kindle-dash/1.0 (personal e-ink dashboard)"}
MOCK = "--mock" in sys.argv
LAST_WEATHER = None
GREY, LIGHT = 60, 185     # secondary text, hairlines

# ---------------------------------------------------------------- fonts
# Inter is downloaded by the workflow into fonts/. DejaVu is the fallback.
INTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Inter.ttf")

def font(size, weight=400):
    try:
        f = ImageFont.truetype(INTER, size)
        try:
            f.set_variation_by_axes([min(32, max(14, size)), weight])
        except Exception:
            pass
        return f
    except OSError:
        name = "DejaVuSans-Bold.ttf" if weight >= 600 else "DejaVuSans.ttf"
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/" + name, size)
        except OSError:
            return ImageFont.load_default()

F = {
    "day": font(38, 700), "date": font(22, 500), "pill": font(18, 600),
    "label": font(14, 700), "temp": font(76, 700), "cond": font(24, 600),
    "body": font(18, 500), "bodym": font(18, 650), "bodyb": font(18, 700),
    "small": font(16, 500), "badge": font(15, 700), "year": font(26, 700),
    "alert": font(19, 700), "power": font(22, 700), "tmhead": font(20, 700), "binbig": font(24, 700), "binhead": font(32, 700),
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

def icon_kind(code, night=False):
    if code in (0, 1):
        return "moon" if night else "sun"
    if code == 2:
        return "partly_night" if night else "partly"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 57:
        return "drizzle"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "thunder"
    return "rain"

def weather(now):
    j = get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": LAT, "longitude": LON, "timezone": "Europe/London", "forecast_days": 2,
        "wind_speed_unit": "mph",
        "current": "temperature_2m,weather_code,is_day",
        "hourly": "precipitation_probability,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset,wind_speed_10m_max",
    }).json()
    c, d, h = j["current"], j["daily"], j["hourly"]
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
    day = lambda k: {"code": d["weather_code"][k], "text": WMO.get(d["weather_code"][k], "?"),
                     "hi": round(d["temperature_2m_max"][k]), "lo": round(d["temperature_2m_min"][k]),
                     "rain": d["precipitation_probability_max"][k] or 0, "wind": round(d["wind_speed_10m_max"][k])}
    return {"now_t": round(c["temperature_2m"]), "now_code": c["weather_code"], "night": not c.get("is_day", 1),
            "today": day(0), "tomorrow": day(1), "rain": rain,
            "sunrise": d["sunrise"][0][11:16], "sunset": d["sunset"][0][11:16]}

def warnings():
    root = ET.fromstring(get(WARNINGS_RSS).content)
    out = []
    for i in root.iter("item"):
        t = i.findtext("title", "").strip()
        if t and t not in out:          # the feed lists the same warning once per time period
            out.append(t)
    return out[:2]

def moon(now):
    days = (now.astimezone(dt.timezone.utc) - dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc)).total_seconds() / 86400
    p = (days % 29.530588) / 29.530588
    names = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
             "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"]
    return names[int((p * 8) + 0.5) % 8]

def bins(now):
    """state: 'tonight' (Wednesday), 'today' (Thursday morning) or 'next'."""
    today = now.date()
    thu = today + dt.timedelta(days=(3 - today.weekday()) % 7)
    if today.weekday() == 3 and now.hour >= 12:
        thu += dt.timedelta(days=7)
    weeks = (thu - BIN_ANCHOR).days // 7
    items = ["Black", "Green", "Food"] if weeks % 2 == 0 else ["Blue", "Food"]
    note = ""
    try:
        hols = get("https://www.gov.uk/bank-holidays.json").json()["england-and-wales"]["events"]
        monday = thu - dt.timedelta(days=3)
        if any(monday <= dt.date.fromisoformat(e["date"]) <= thu for e in hols):
            note = "Bank holiday this week, collection may be a day late"
    except Exception:
        pass
    if (thu.month == 12 and thu.day >= 18) or (thu.month == 1 and thu.day <= 6):
        note = "Christmas period, collections may change, check MyDudley"
    state = "tonight" if today.weekday() == 2 else "today" if (today.weekday() == 3 and now.hour < 12) else "next"
    return {"state": state, "date": thu, "items": items, "note": note}

def trains():
    key = os.environ.get("RAIL_KEY")
    if not key:
        return None
    url = f"https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/LDBWS/api/20220120/GetDepartureBoard/{STATION}"
    r = requests.get(url, headers={"x-apikey": key, **UA}, params={"numRows": 6}, timeout=25)
    r.raise_for_status()
    j = r.json()
    rows = []
    for s in (j.get("trainServices") or [])[:6]:
        dest = (s.get("destination") or [{}])[0].get("locationName", "?")
        etd = "Cancelled" if s.get("isCancelled") else s.get("etd", "")
        rows.append((s.get("std", ""), dest, etd))
    msg = ""
    if j.get("nrccMessages"):
        msg = re.sub("<[^>]+>", "", (j["nrccMessages"][0] or {}).get("Value", "")).strip()
    return rows, msg

def tomtom(bbox):
    key = os.environ.get("TOMTOM_KEY")
    if not key:
        return None
    fields = "{incidents{geometry{type,coordinates},properties{iconCategory,magnitudeOfDelay,events{description},from,to,delay,roadNumbers}}}"
    j = get("https://api.tomtom.com/traffic/services/5/incidentDetails", params={
        "key": key, "bbox": bbox, "fields": fields, "language": "en-GB", "timeValidityFilter": "present"}).json()
    out = []
    for i in j.get("incidents", []):
        p = i["properties"]
        c = (i.get("geometry") or {}).get("coordinates") or []
        pts = [c] if c and isinstance(c[0], (int, float)) else c
        p["km"] = min((km(pt[1], pt[0]) for pt in pts), default=99)   # nearest point to home
        out.append(p)
    return out

def describe(p, local=False):
    """Returns (badge, text)."""
    roads = [r for r in (p.get("roadNumbers") or []) if not r.upper().startswith("E")]
    fr, to = (p.get("from") or "").strip(), (p.get("to") or "").strip()
    if not roads and fr.upper() in MOTORWAYS:
        roads = [fr.upper()]
    badge = roads[0] if roads else ("Local" if local else "")
    ev = "; ".join(e["description"] for e in (p.get("events") or []))
    ev = ev.replace("Closed; Roadworks", "Closed for roadworks")
    if fr and fr == to:
        where = f"on the {fr}" if fr.upper() in MOTORWAYS else f"on {fr}"
    else:
        where = " to ".join(x for x in (fr, to) if x)
    mins = round((p.get("delay") or 0) / 60)
    delay = f", +{mins} min" if mins >= 1 else ""
    return badge, f"{ev}: {where}{delay}" if where else f"{ev}{delay}"

def roads():
    mw = tomtom(MOTORWAY_BBOX)
    if mw is None:
        return None
    def is_mw(p):
        if any(r.upper().replace(" TOLL", "") in MOTORWAYS for r in (p.get("roadNumbers") or [])):
            return True
        # junction links often have no road number, only "M5 to M5"
        ends = {(p.get("from") or "").strip().upper(), (p.get("to") or "").strip().upper()}
        return bool(ends) and ends <= set(MOTORWAYS)
    rank = lambda p: (p.get("iconCategory") != 8, p.get("iconCategory") != 1, -(p.get("magnitudeOfDelay") or 0))
    def dedupe(items):
        seen, out = set(), []
        for p in items:
            k = (frozenset((p.get("from"), p.get("to"))), p.get("iconCategory"))
            if k not in seen:
                seen.add(k); out.append(p)
        return out
    mws = dedupe(sorted([p for p in mw if is_mw(p) and p.get("iconCategory") in (1, 6, 7, 8, 9, 14)], key=rank))
    loc = [p for p in tomtom(LOCAL_BBOX) if not is_mw(p) and p.get("iconCategory") in (1, 6, 7, 8, 9)]
    for p in sorted(loc, key=lambda p: p["km"]):       # listed in the build log, for checking
        print(f'local: {p["km"]:.1f} km  cat {p.get("iconCategory")}  {describe(p, True)[1]}')
    # closures first, then nearest
    loc = dedupe(sorted(loc, key=lambda p: (p.get("iconCategory") != 8, p["km"])))
    loc = [p for p in loc if p.get("iconCategory") in (1, 8) or p["km"] <= 1.5]
    return ([describe(p) for p in mws[:3]] or [("M5", "M5, M6, M42: no reported problems")],
            [describe(p, True) for p in loc[:3]] or [("Local", "No road closures reported nearby")])

def km(la, lo):
    return 6371 * math.acos(min(1, math.sin(math.radians(LAT)) * math.sin(math.radians(la)) +
                                math.cos(math.radians(LAT)) * math.cos(math.radians(la)) * math.cos(math.radians(lo - LON))))

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
            kind = "Planned cut" if (r.get("planned") or "").lower() in ("true", "yes", "1") else "Power cut"
            etr = (r.get("etr") or "").strip()
            out.append(f"{kind} near {r.get('postcode', '').strip()} ({dist:.1f} km)" + (f", back by {etr[11:16] or etr}" if etr else ""))
    return out[:2] or [f"No power cuts within {POWER_RADIUS_KM} km"]

def fuel():
    """Cheapest nearby fuel, from fuel.json. Fuel Finder blocks GitHub's servers,
    so a script on Neil's PC (pc/fuel.ps1) fetches the prices and saves that file here."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fuel.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig") as f:
        j = json.load(f)
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(j["updated"].replace("Z", "+00:00"))
    if age > dt.timedelta(hours=24):
        return None
    def short(name):
        name = re.sub(r"\b(ltd|limited|plc|llp|trading|services?|stations?|filling|garage|retail|uk|group|petroleum|stores)\b\.?", "", name, flags=re.I)
        return re.sub(r"\s+", " ", name).strip(" ,&-") or name
    def where(e):
        name = short(e.get("name") or e["brand"])
        place = (e.get("place") or "").strip()
        if place and place.lower() not in name.lower():
            name += f", {place}"
        return name + (f" ({e['km']:.1f} km)" if e.get("km") is not None else "")
    parts = [f"{k.title()} {j[k]['price']:.1f}p {where(j[k])}" for k in ("unleaded", "diesel") if j.get(k)]
    if parts and age > dt.timedelta(hours=2):
        parts[-1] += f" (as of {dt.datetime.fromisoformat(j['updated'].replace('Z', '+00:00')).astimezone(TZ):%H:%M})"
    return parts or None

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
    return str(e["year"]), e["text"]

def light_level(now, w):
    """Front-light level for the Kindle, from the time of day and today's sunrise/sunset."""
    if now.hour >= NIGHT_FROM or now.hour < NIGHT_UNTIL:
        return FRONTLIGHT["night"]
    if not w:
        return FRONTLIGHT["evening"] if now.hour >= 17 else FRONTLIGHT["dawn"] if now.hour < 9 else FRONTLIGHT["day"]
    t = now.hour * 60 + now.minute
    rise = int(w["sunrise"][:2]) * 60 + int(w["sunrise"][3:])
    sets = int(w["sunset"][:2]) * 60 + int(w["sunset"][3:])
    if t < rise - 30:
        return FRONTLIGHT["dawn"]
    if t < rise + 30 or sets - 30 <= t < sets + 30:
        return FRONTLIGHT["twilight"]
    if t < sets - 30:
        return FRONTLIGHT["day"]
    return FRONTLIGHT["evening"]

def art(now):
    """Painting of the day from the Cleveland Museum of Art (public domain): (greyscale image, caption)."""
    base = "https://openaccess-api.clevelandart.org/api/artworks/"
    q = {"q": ART_QUERY, "type": "Painting", "cc0": 1, "has_image": 1, "limit": 1}
    total = get(base, params=q).json()["info"]["total"]
    if not total:
        raise RuntimeError("no artworks found")
    start = (now.timetuple().tm_yday * 37) % total     # same painting all day
    for k in range(5):
        d = get(base, params={**q, "skip": (start + k) % total}).json()["data"][0]
        url = ((d.get("images") or {}).get("web") or {}).get("url")
        if url:
            img = Image.open(io.BytesIO(get(url).content)).convert("L")
            who = ((d.get("creators") or [{}])[0].get("description") or "Unknown artist").split(" (")[0]
            when = d.get("creation_date") or ""
            return img, f"{d.get('title', 'Untitled')} · {who}" + (f", {when}" if when else "")
    raise RuntimeError("no image found")

def time_of_day(h):
    return ("early" if h < 7 else "morning" if h < 12 else "afternoon" if h < 15
            else "late afternoon" if h < 18 else "evening")

# ---------------------------------------------------------------- icons
def draw_icon(kind, size):
    """Black-and-white weather icon, drawn at 4x and scaled down for smooth edges."""
    S = size * 4
    im = Image.new("L", (S, S), 255)
    d = ImageDraw.Draw(im)
    lw = max(4, S // 16)

    def circle(cx, cy, r, fill=None, outline=0, width=lw):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline, width=width)

    def sun(cx, cy, r):
        circle(cx, cy, r, fill=255)
        for i in range(8):
            a = i * math.pi / 4
            d.line((cx + math.cos(a) * r * 1.45, cy + math.sin(a) * r * 1.45,
                    cx + math.cos(a) * r * 1.95, cy + math.sin(a) * r * 1.95), fill=0, width=lw)

    def moon_(cx, cy, r):
        circle(cx, cy, r, fill=0, outline=0)
        d.ellipse((cx - r * 0.45, cy - r * 1.25, cx + r * 1.55, cy + r * 0.75), fill=255)

    def cloud(x0, y0, w):
        # outline drawn by stacking a black shape and a slightly smaller white one
        def shape(inset, fill):
            h = w * 0.62
            parts = [(x0 + w * 0.30, y0 + h * 0.55, w * 0.22), (x0 + w * 0.55, y0 + h * 0.40, w * 0.28),
                     (x0 + w * 0.78, y0 + h * 0.62, w * 0.19)]
            for cx, cy, r in parts:
                d.ellipse((cx - r + inset, cy - r + inset, cx + r - inset, cy + r - inset), fill=fill)
            d.rounded_rectangle((x0 + w * 0.08 + inset, y0 + h * 0.55 + inset, x0 + w * 0.97 - inset,
                                 y0 + h * 0.95 - inset), radius=w * 0.18, fill=fill)
        shape(0, 0)
        shape(lw, 255)
        return y0 + w * 0.62 * 0.95

    if kind == "sun":
        sun(S / 2, S / 2, S * 0.22)
    elif kind == "moon":
        moon_(S / 2, S / 2, S * 0.30)
    elif kind in ("partly", "partly_night"):
        (sun if kind == "partly" else moon_)(S * 0.36, S * 0.34, S * (0.16 if kind == "partly" else 0.2))
        cloud(S * 0.22, S * 0.36, S * 0.74)
    else:
        bottom = cloud(S * 0.08, S * 0.10, S * 0.84)
        if kind == "rain":
            for i in range(3):
                x = S * (0.30 + i * 0.2)
                d.line((x, bottom + S * 0.06, x - S * 0.07, bottom + S * 0.24), fill=0, width=lw)
        elif kind == "drizzle":
            for i in range(3):
                for j in range(2):
                    circle(S * (0.30 + i * 0.2) - j * S * 0.05, bottom + S * (0.09 + j * 0.13), lw * 0.8, fill=0)
        elif kind == "snow":
            for i in range(3):
                cx, cy, r = S * (0.28 + i * 0.22), bottom + S * 0.15, S * 0.07
                for a in (0, math.pi / 3, 2 * math.pi / 3):
                    d.line((cx - math.cos(a) * r, cy - math.sin(a) * r, cx + math.cos(a) * r, cy + math.sin(a) * r),
                           fill=0, width=max(3, lw * 2 // 3))
        elif kind == "thunder":
            x, y = S * 0.5, bottom + S * 0.02
            d.polygon([(x + S * 0.04, y), (x - S * 0.10, y + S * 0.16), (x - S * 0.01, y + S * 0.16),
                       (x - S * 0.06, y + S * 0.30), (x + S * 0.10, y + S * 0.11), (x + S * 0.01, y + S * 0.11)], fill=0)
        elif kind == "fog":
            for i in range(3):
                y = bottom + S * (0.06 + i * 0.085)
                d.line((S * (0.14 + 0.06 * (i % 2)), y, S * (0.86 - 0.06 * (i % 2)), y), fill=0, width=lw)
    return im.resize((size, size), Image.LANCZOS)

def draw_bin(kind, size):
    """Wheelie bin (or food caddy) icon, shaded by bin colour, drawn at 4x for smooth edges."""
    S = size * 4
    im = Image.new("L", (S, S), 255)
    d = ImageDraw.Draw(im)
    lw = S // 18
    shade = {"Black": 40, "Green": 120, "Blue": 185, "Food": 90}[kind]
    if kind == "Food":
        # small caddy with handle
        d.rounded_rectangle((S * .22, S * .38, S * .78, S * .92), radius=S * .06, fill=shade, outline=0, width=lw)
        d.rounded_rectangle((S * .18, S * .30, S * .82, S * .40), radius=S * .03, fill=shade, outline=0, width=lw)
        d.arc((S * .30, S * .10, S * .70, S * .50), 180, 360, fill=0, width=lw)
    else:
        d.polygon([(S * .20, S * .22), (S * .80, S * .22), (S * .74, S * .88), (S * .26, S * .88)], fill=shade, outline=0)
        d.line([(S * .20, S * .22), (S * .26, S * .88), (S * .74, S * .88), (S * .80, S * .22)], fill=0, width=lw)
        d.rounded_rectangle((S * .14, S * .12, S * .86, S * .23), radius=S * .03, fill=shade, outline=0, width=lw)   # lid
        d.rectangle((S * .42, S * .07, S * .58, S * .13), fill=0)                                                   # handle
        for cx in (S * .30, S * .70):
            d.ellipse((cx - S * .08, S * .82, cx + S * .08, S * .98), fill=0)                                        # wheels
            d.ellipse((cx - S * .03, S * .87, cx + S * .03, S * .93), fill=255)
    return im.resize((size, size), Image.LANCZOS)

# ---------------------------------------------------------------- drawing
class Page:
    def __init__(self):
        self.img = Image.new("L", (W, H), 255)
        self.d = ImageDraw.Draw(self.img)
        self.y = 0
        self.limit = H - FOOTER

    def tw(self, s, f):
        return self.d.textlength(s, font=F[f])

    def lh(self, f):
        a, b = F[f].getmetrics()
        return a + b

    def wrap(self, text, f, width, max_lines):
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if self.tw(t, f) <= width:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            while lines[-1] and self.tw(lines[-1] + "...", f) > width:
                lines[-1] = lines[-1][:-1].rstrip()
            lines[-1] += "..."
        return lines

    def fits(self, h):
        return self.y + h <= self.limit

    def text(self, s, f="body", x=M, max_lines=1, gap=3, fill=0, width=None):
        width = width or W - x - M
        for line in self.wrap(s, f, width, max_lines):
            h = self.lh(f)
            if not self.fits(h):
                return False
            self.d.text((x, self.y), line, font=F[f], fill=fill)
            self.y += h + gap
        return True

    def label(self, s, top_gap=10):
        """Small letter-spaced section label with a hairline running to the margin."""
        self.y += top_gap
        if not self.fits(40):
            self.y = self.limit
            return False
        x = M
        for ch in s.upper():
            self.d.text((x, self.y), ch, font=F["label"], fill=GREY)
            x += self.tw(ch, "label") + 2.2
        mid = self.y + self.lh("label") // 2 + 1
        self.d.line((x + 10, mid, W - M, mid), fill=LIGHT, width=1)
        self.y += self.lh("label") + 6
        return True

    def rule(self, gap=14):
        self.y += gap
        self.d.line((M, self.y, W - M, self.y), fill=LIGHT, width=1)

    def pill(self, x, y, s, f="pill", inverted=False, pad=12, h=None):
        h = h or self.lh(f) + 4
        w = self.tw(s, f) + pad * 2
        if inverted:
            self.d.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=0)
        else:
            self.d.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, outline=0, width=2)
        self.d.text((x + pad, y + 1), s, font=F[f], fill=255 if inverted else 0)
        return w

    def badge(self, x, y, s):
        """Small black road-number tag."""
        w = max(52, self.tw(s, "badge") + 14)
        h = self.lh("body") - 2
        self.d.rounded_rectangle((x, y + 1, x + w, y + h), radius=5, fill=0)
        self.d.text((x + (w - self.tw(s, "badge")) / 2, y + 3), s, font=F["badge"], fill=255)
        return w

    def alert(self, s):
        lines = self.wrap(s, "alert", W - 2 * M - 64, 2)
        h = len(lines) * 26 + 18
        if not self.fits(h):
            return
        self.d.rounded_rectangle((M, self.y, W - M, self.y + h), radius=14, fill=0)
        # warning triangle
        cx, cy = M + 26, self.y + h / 2
        self.d.polygon([(cx, cy - 12), (cx - 13, cy + 10), (cx + 13, cy + 10)], fill=255)
        self.d.rectangle((cx - 1.5, cy - 4, cx + 1.5, cy + 3), fill=0)
        self.d.rectangle((cx - 1.5, cy + 5, cx + 1.5, cy + 7), fill=0)
        yy = self.y + 9
        for l in lines:
            self.d.text((M + 50, yy), l, font=F["alert"], fill=255)
            yy += 26
        self.y += h + 10

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

    # ---- header: day, date, time-of-day pill, day progress
    p.y = 18
    d.text((M, p.y), f"{now:%A}", font=F["day"], fill=0)
    dx = M + p.tw(f"{now:%A}", "day") + 12
    d.text((dx, p.y + 14), f"{now.day} {now:%B}", font=F["date"], fill=GREY)
    tod = time_of_day(now.hour)
    pw = p.tw(tod, "pill") + 24
    p.pill(W - M - pw, p.y + 8, tod)
    p.y += 58
    # day timeline: midnight to midnight, night shaded from today's sunrise/sunset, dot = now
    w = section(weather, now)
    global LAST_WEATHER
    LAST_WEATHER = w
    x0, x1, ty = M + 6, W - M - 6, p.y + 10
    span = x1 - x0
    xm = lambda mins: x0 + span * mins / 1440
    rise, sets = (7 * 60, 17 * 60)
    if w:
        rise = int(w["sunrise"][:2]) * 60 + int(w["sunrise"][3:])
        sets = int(w["sunset"][:2]) * 60 + int(w["sunset"][3:])
    d.rounded_rectangle((x0, ty - 5, xm(rise), ty + 5), radius=5, fill=170)      # night before sunrise
    d.rounded_rectangle((xm(sets), ty - 5, x1, ty + 5), radius=5, fill=170)      # night after sunset
    d.line((xm(rise), ty, xm(sets), ty), fill=0, width=2)                         # daylight
    for hh, lab in ((6, "6am"), (12, "noon"), (18, "6pm")):
        xx = xm(hh * 60)
        d.line((xx, ty + 7, xx, ty + 12), fill=GREY, width=2)
        d.text((xx - p.tw(lab, "small") / 2, ty + 13), lab, font=F["small"], fill=GREY)
    nx = xm(now.hour * 60 + now.minute)
    d.ellipse((nx - 9, ty - 9, nx + 9, ty + 9), fill=0, outline=255, width=3)
    p.y += 42

    for wtxt in section(warnings) or []:
        p.alert(wtxt)
    pw_ = section(power)
    if pw_ and not pw_[0].startswith("No power cuts"):
        for s_ in pw_:
            p.alert(s_)

    # ---- weather
    p.label("Weather", top_gap=4)
    if w:
        top = p.y
        icon = draw_icon(icon_kind(w["now_code"], w["night"]), 86)
        p.img.paste(icon, (M - 4, top))
        t = f"{w['now_t']}°"
        tx = M + 104
        d.text((tx, top - 8), t, font=F["temp"], fill=0)
        cx = tx + p.tw(t, "temp") + 18
        tdy = w["today"]
        d.text((cx, top + 6), WMO.get(w["now_code"], ""), font=F["cond"], fill=0)
        d.text((cx, top + 40), f"High {tdy['hi']}°  Low {tdy['lo']}°", font=F["body"], fill=0)
        d.text((cx, top + 66), f"Rain {tdy['rain']}%  Wind {tdy['wind']} mph", font=F["body"], fill=GREY)
        split = M + (W - 2 * M) * 2 // 3          # today gets 2/3, tomorrow 1/3
        p.y = top + 94
        p.text(w["rain"], "bodym", max_lines=2, width=split - M - 12)
        # tomorrow column
        tm = w["tomorrow"]
        rx = split + 18
        d.line((split, top, split, p.y - 4), fill=LIGHT, width=2)
        d.text((rx, top - 2), "Tomorrow", font=F["tmhead"], fill=0)
        p.img.paste(draw_icon(icon_kind(tm["code"]), 56), (rx, top + 28))
        d.text((rx + 66, top + 32), f"{tm['hi']}° / {tm['lo']}°", font=F["bodyb"], fill=0)
        d.text((rx + 66, top + 56), f"Rain {tm['rain']}%", font=F["body"], fill=GREY)
        tt = p.wrap(tm["text"], "body", W - M - rx, 1)[0]
        d.text((rx, top + 92), tt, font=F["body"], fill=0)
        p.y = max(p.y, top + 120) + 2
        p.text(f"Sunrise {w['sunrise']}  ·  Sunset {w['sunset']}  ·  {moon(now)}", "small", fill=GREY)
    else:
        p.text("Weather unavailable")

    # ---- bins (own section, highlighted the day before and the morning of)
    b = bins(now) if not MOCK else MOCK_DATA["bins"]
    p.label("Bins")
    names = {"Black": "Black", "Green": "Green", "Blue": "Blue", "Food": "Food"}
    alert = b["state"] in ("tonight", "today")
    h = 104 + (22 if b["note"] else 0)
    if p.fits(h):
        top = p.y
        fg = 255 if alert else 0
        if alert:
            d.rounded_rectangle((M, top, W - M, top + h), radius=18, fill=0)
        days = (b["date"] - now.date()).days
        head = ("Bins out tonight" if b["state"] == "tonight" else "Bin day today") if alert else f"{b['date']:%A} {b['date'].day} {b['date']:%b}"
        sub = "Collected tomorrow morning" if b["state"] == "tonight" else "Collection today" if alert else \
              ("tomorrow" if days == 1 else f"in {days} days")
        tx = M + (20 if alert else 0)
        d.text((tx, top + 18), head, font=F["binhead"], fill=fg)
        d.text((tx, top + 58), sub, font=F["body"], fill=fg if alert else GREY)
        # bin icons, right-aligned
        x = W - M - (20 if alert else 0)
        for it in reversed(b["items"]):
            ic = draw_bin(it, 64)
            x -= 64
            if alert:
                d.rounded_rectangle((x - 6, top + 8, x + 70, top + 96), radius=12, fill=255)
            p.img.paste(ic, (x, top + 10))
            lbl = names[it]
            d.text((x + 32 - p.tw(lbl, "badge") / 2, top + 76), lbl, font=F["badge"], fill=0)
            x -= 20
        if b["note"]:
            d.text((tx, top + 104), b["note"], font=F["small"], fill=fg if alert else GREY)
        p.y = top + h

    # ---- roads
    r = section(roads)
    p.label("Roads and fuel")
    if r is None:
        p.text("Traffic unavailable", fill=GREY)
    else:
        for badge, txt in r[1][:3] + r[0][:2]:
            if not p.fits(p.lh("body")):
                break
            p.badge(M, p.y, badge or "?")
            p.text(txt, x=M + 76)

    fu = section(fuel)
    if fu and p.fits(p.lh("body") + 10):
        p.y += 10                              # small gap: fuel isn't a road item
        p.badge(M, p.y, "Fuel")
        p.text("Cheapest: " + "  ·  ".join(fu), x=M + 76, max_lines=2)

    # ---- trains
    p.label(f"Trains from {STATION_NAME}")
    tr = section(trains)
    if tr is None:
        p.text("Waiting for rail key" if not os.environ.get("RAIL_KEY") and not MOCK else "Departures unavailable",
               fill=GREY)
    else:
        rows, msg = tr
        for std, dest, etd in rows:
            if not p.fits(p.lh("body")):
                break
            y = p.y
            d.text((M, y), std, font=F["bodyb"], fill=0)
            if etd in ("On time", ""):
                sw = p.tw("On time", "body")
                d.text((W - M - sw, y), "On time", font=F["body"], fill=GREY)
            else:
                sw = p.tw(etd, "badge") + 20
                d.rounded_rectangle((W - M - sw, y + 1, W - M, y + 24), radius=11, fill=0)
                d.text((W - M - sw + 10, y + 4), etd, font=F["badge"], fill=255)
            dl = p.wrap(dest, "body", W - 2 * M - 74 - sw - 12, 1)[0]
            d.text((M + 74, y), dl, font=F["body"], fill=0)
            p.y += p.lh("body") + 6
        if not rows:
            p.text("No departures listed", fill=GREY)
        if msg:
            p.text(msg, "small", max_lines=1, fill=GREY)

    # ---- painting of the day, faded behind everything
    a = section(art, now)
    img = p.img
    if a:
        pic, caption = a
        pic = ImageEnhance.Contrast(ImageOps.fit(pic, (W, H), Image.LANCZOS)).enhance(1.15)
        faded = pic.point(lambda v: int(255 - (255 - v) * ART_STRENGTH))
        img = ImageChops.darker(faded, p.img)
        d = ImageDraw.Draw(img)
    # ---- footer: painting note on the left, power + update time on the right
    # power status, bold on the left
    ptxt = pw_[0] if pw_ else "Power cut info unavailable"
    d.text((M, H - 34), ptxt, font=F["power"], fill=0)
    upd = f"Updated {now:%H:%M}"
    ux = W - M - p.tw(upd, "small")
    d.text((ux, H - 28), upd, font=F["small"], fill=GREY)
    # refresh symbol: tapping the screen refreshes
    cx, cy, r = ux - 14, H - 18, 7
    d.arc((cx - r, cy - r, cx + r, cy + r), 50, 330, fill=GREY, width=2)
    ax, ay = cx + r * math.cos(math.radians(50)), cy + r * math.sin(math.radians(50))
    d.polygon([(ax - 1, ay - 6), (ax + 5, ay + 1), (ax - 4, ay + 3)], fill=GREY)
    if a:
        note = p.wrap("Painting: " + caption, "small", W - 2 * M, 1)[0]
        d.text((M, H - 60), note, font=F["small"], fill=GREY)
    return img

MOCK_DATA = {
    "warnings": ["Yellow warning of fog affecting West Midlands"],
    "bins": {"state": "tonight", "date": dt.date(2026, 11, 12), "items": ["Black", "Green", "Food"], "note": ""},
    "weather": {"now_t": 6, "now_code": 2, "night": False, "rain": "Dry until 15:00, then rain likely",
                "today": {"code": 80, "text": "Light showers", "hi": 10, "lo": 4, "rain": 70, "wind": 18},
                "tomorrow": {"code": 45, "text": "Fog", "hi": 9, "lo": 2, "rain": 10, "wind": 6},
                "sunrise": "07:17", "sunset": "16:26"},
    "trains": ([("07:41", "Birmingham Snow Hill", "On time"), ("07:52", "Kidderminster", "07:58"),
                ("08:11", "Stratford-upon-Avon", "On time"), ("08:22", "Worcester Foregate Street", "Cancelled")], ""),
    "roads": ([("M42", "Lane closed: M40 to A34 J4"), ("M6", "Hard shoulder closed: A38 J6 to M42 J4A"),
               ("M6", "Lane closed: Coleshill to A452 J5")],
              [("B4183", "Closed: Hagley Road at Albert Road"), ("Local", "Closed for roadworks: Stourbridge Road (A458) to Pedmore Road (A4036)")]),
    "fuel": ["Unleaded 131.9p Asda Halesowen Superstore", "Diesel 138.9p Tesco Extra Merry Hill"],
    "art": (Image.open("/tmp/art/wave.jpg").convert("L") if os.path.exists("/tmp/art/wave.jpg") else Image.new("L", (10, 10), 255),
            "The Great Wave off Kanagawa · Katsushika Hokusai, ca. 1830–32"),
    "power": ["No power cuts within 5 km"],
    "news": ("Halesowen News", ["LIVE: Remembrance Sunday parades in Worcestershire and the Black Country",
                                "Thousands gather in Dudley for Remembrance Sunday parade and service",
                                "Police appeal after staff member assaulted during shop robbery",
                                "Severn Valley Railway announces special guest for 40th diesel gala"]),
    "on_this_day": ("1971", "The English rock group Led Zeppelin released their fourth album, which became one of the best-selling albums worldwide."),
}

if __name__ == "__main__":
    now = dt.datetime.now(TZ)
    if MOCK:
        now = now.replace(year=2026, month=11, day=11, hour=7, minute=30)
    img = build(now)
    os.makedirs("out", exist_ok=True)
    img.save("out/dash.png", optimize=True)
    with open("out/light.txt", "w") as f:
        f.write(str(light_level(now, LAST_WEATHER)))
    print("wrote out/dash.png")
