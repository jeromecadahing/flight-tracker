#!/usr/bin/env python3
"""
Cheapest London -> USA or London -> Europe flight tracker with a 7-day price forecast.

Run it once a day (see README.md for cron / Task Scheduler). Each run:
  1. asks SerpApi's Google Flights API for the cheapest fare from London to
     a set of US airports on your travel date,
  2. appends the result to prices.csv,
  3. forecasts the next 7 days and tells you whether to book or wait.

Needs only the Python standard library. Matplotlib is optional (--chart).

Set your key first:   export SERPAPI_KEY="your_key"      (macOS / Linux)
                      setx SERPAPI_KEY "your_key"        (Windows, then reopen terminal)

Examples:
  python flight_tracker.py --date 2027-03-14                    # USA (default)
  python flight_tracker.py --region europe --date 2027-03-14    # Europe
  python flight_tracker.py --days-ahead 45 --to JFK,EWR
  python flight_tracker.py --date 2027-03-14 --return 2027-03-28 --chart
  python flight_tracker.py --forecast-only          # no API call, just re-forecast
"""

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------
# Defaults — edit these to taste
# --------------------------------------------------------------------------
LONDON_AIRPORTS = "LHR,LGW,STN,LTN,LCY"
REGIONS = {
    "us": {"name": "USA",
           "airports": "JFK,EWR,BOS,ORD,MIA,MCO,LAX"},
    "europe": {"name": "Europe",
               "airports": "BCN,FCO,LIS,CDG,AMS,DUB,ATH"},
}
# Google Flights takes at most 7 airports on each side of one search.
# Longer lists still work: they're split into groups of 7, one search each.
MAX_AIRPORTS_PER_SEARCH = 7
CURRENCY = "GBP"
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.csv")
FORECAST_DAYS = 7

FIELDS = [
    "region", "checked_on", "travel_date", "return_date", "price", "currency", "airline",
    "route", "stops", "duration_min", "typical_low", "typical_high", "price_level",
]


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------
class NoResults(Exception):
    pass


def chunks(codes, size=MAX_AIRPORTS_PER_SEARCH):
    items = [c.strip().upper() for c in codes.split(",") if c.strip()]
    return [",".join(items[i:i + size]) for i in range(0, len(items), size)]


def search_all(api_key, origin, dest, travel_date, return_date=None, adults=1):
    """Search every origin/destination group and keep the cheapest result."""
    best_row, best_hist, searches = None, [], 0
    for o in chunks(origin):
        for d in chunks(dest):
            searches += 1
            try:
                row, hist = fetch_cheapest(api_key, o, d, travel_date, return_date, adults)
            except NoResults:
                print(f"  (no flights found for {o} to {d})")
                continue
            if best_row is None or row["price"] < best_row["price"]:
                best_row, best_hist = row, hist
    if best_row is None:
        sys.exit("No flights found for that date on any of those routes. "
                 "Try another --date or different airports with --to.")
    if searches > 1:
        print(f"  (used {searches} searches)")
    return best_row, best_hist


def fetch_cheapest(api_key, origin, dest, travel_date, return_date=None, adults=1):
    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": dest,
        "outbound_date": travel_date,
        "type": "1" if return_date else "2",  # 1 = round trip, 2 = one way
        "currency": CURRENCY,
        "hl": "en",
        "gl": "uk",
        "adults": str(adults),
        "api_key": api_key,
    }
    if return_date:
        params["return_date"] = return_date

    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        sys.exit(f"SerpApi returned HTTP {e.code}: {body[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach SerpApi: {e.reason}")

    if "error" in data:
        if "hasn't returned any results" in data["error"]:
            raise NoResults(data["error"])
        sys.exit(f"SerpApi error: {data['error']}")

    options = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    options = [o for o in options if isinstance(o.get("price"), (int, float))]
    if not options:
        raise NoResults("No priced flights came back.")

    best = min(options, key=lambda o: o["price"])
    legs = best.get("flights") or []
    airlines = []
    for leg in legs:
        name = leg.get("airline")
        if name and name not in airlines:
            airlines.append(name)
    route = ""
    if legs:
        route = "-".join(
            [legs[0].get("departure_airport", {}).get("id", "?")]
            + [leg.get("arrival_airport", {}).get("id", "?") for leg in legs]
        )

    insights = data.get("price_insights") or {}
    typical = insights.get("typical_price_range") or [None, None]
    history = insights.get("price_history") or []  # [[unix_ts, price], ...]

    row = {
        "checked_on": dt.date.today().isoformat(),
        "travel_date": travel_date,
        "return_date": return_date or "",
        "price": best["price"],
        "currency": CURRENCY,
        "airline": " + ".join(airlines),
        "route": route,
        "stops": max(len(legs) - 1, 0),
        "duration_min": best.get("total_duration", ""),
        "typical_low": typical[0] if len(typical) > 0 else "",
        "typical_high": typical[1] if len(typical) > 1 else "",
        "price_level": insights.get("price_level", ""),
    }
    return row, history


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
def load_rows(path=DATA_FILE):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_row(row, path=DATA_FILE):
    rows = load_rows(path)
    # one row per (check date, trip) — re-running the same day replaces it
    def key(r):
        return (r.get("region") or "us", r["checked_on"], r["travel_date"], r.get("return_date") or "")
    rows = [r for r in rows if key(r) != key(row)]
    rows.append({k: row.get(k, "") for k in FIELDS})
    rows.sort(key=lambda r: (r.get("region") or "us", r["travel_date"], r["checked_on"]))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------
# Forecast — Holt's linear exponential smoothing (level + trend)
# --------------------------------------------------------------------------
def daily_series(points):
    """points: {date: price}. Returns consecutive daily list, gaps interpolated."""
    if not points:
        return [], []
    days = sorted(points)
    start, end = days[0], days[-1]
    out_dates, out_vals = [], []
    d = start
    while d <= end:
        if d in points:
            v = points[d]
        else:
            prev = max(x for x in days if x < d)
            nxt = min(x for x in days if x > d)
            frac = (d - prev).days / (nxt - prev).days
            v = points[prev] + frac * (points[nxt] - points[prev])
        out_dates.append(d)
        out_vals.append(float(v))
        d += dt.timedelta(days=1)
    return out_dates, out_vals


def holt(series, alpha, beta):
    level, trend = series[0], (series[1] - series[0]) if len(series) > 1 else 0.0
    sse = 0.0
    for y in series[1:]:
        pred = level + trend
        sse += (y - pred) ** 2
        new_level = alpha * y + (1 - alpha) * (level + trend)
        trend = beta * (new_level - level) + (1 - beta) * trend
        level = new_level
    return level, trend, sse


def forecast(series, horizon=FORECAST_DAYS):
    """Returns list of (point, low, high) for the next `horizon` days."""
    n = len(series)
    if n < 3:
        return []
    best = None
    grid = [i / 10 for i in range(1, 10)]
    for a in grid:
        for b in grid:
            level, trend, sse = holt(series, a, b)
            if best is None or sse < best[0]:
                best = (sse, level, trend)
    sse, level, trend = best
    # damp the trend a little so a few noisy days don't explode the line
    rmse = math.sqrt(sse / max(n - 1, 1))
    out, damp = [], 0.0
    for h in range(1, horizon + 1):
        damp += 0.9 ** h
        point = max(level + damp * trend, 1.0)
        spread = 1.96 * rmse * math.sqrt(h)
        out.append((point, max(point - spread, 1.0), point + spread))
    return out


def build_points(rows, region, travel_date, return_date, google_history):
    points = {}
    # Google's own price history for this trip (if any) seeds the model
    for item in google_history or []:
        try:
            ts, price = item
            points[dt.date.fromtimestamp(int(ts))] = float(price)
        except (TypeError, ValueError):
            continue
    # our own checks win over Google's numbers for the same day
    for r in rows:
        if ((r.get("region") or "us") == region and r["travel_date"] == travel_date
                and (r.get("return_date") or "") == (return_date or "")):
            points[dt.date.fromisoformat(r["checked_on"])] = float(r["price"])
    return points


# --------------------------------------------------------------------------
# Advice
# --------------------------------------------------------------------------
def advise(latest, series, fc, typical_low, days_to_go):
    recent_low = min(series[-14:]) if series else latest
    in_week = fc[-1][0] if fc else latest
    if typical_low and latest <= float(typical_low):
        return "BOOK: today's fare is below Google's typical range for this trip."
    if days_to_go is not None and days_to_go <= 21:
        return "BOOK SOON: under 3 weeks to go, fares usually climb from here."
    if latest <= recent_low * 1.02:
        return "GOOD: this is at or near the lowest price in the last two weeks."
    if fc and in_week < latest * 0.95:
        return "WAIT: the trend points lower over the next week — check again tomorrow."
    if fc and in_week > latest * 1.05:
        return "BOOK SOON: the trend points higher over the next week."
    return "HOLD: about average, no strong signal either way. Keep checking."


def save_chart(dates, series, fc, path, region_name):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(matplotlib not installed — skipping chart. pip install matplotlib)")
        return
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(dates, series, marker="o", ms=3, label="Cheapest fare")
    if fc:
        f_dates = [dates[-1] + dt.timedelta(days=i) for i in range(1, len(fc) + 1)]
        ax.plot([dates[-1]] + f_dates, [series[-1]] + [p for p, _, _ in fc],
                linestyle="--", label="Forecast")
        ax.fill_between(f_dates, [lo for _, lo, _ in fc], [hi for _, _, hi in fc],
                        alpha=0.2, label="Likely range")
    ax.set_ylabel(f"Price ({CURRENCY})")
    ax.set_title(f"London to {region_name}, cheapest fare")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    print(f"Chart saved to {path}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Cheapest London to USA or Europe flight, checked daily.")
    p.add_argument("--region", choices=sorted(REGIONS), default="us",
                   help="us (default) or europe — each keeps its own history")
    p.add_argument("--date", help="Outbound date YYYY-MM-DD (fixed trip — best for forecasting)")
    p.add_argument("--days-ahead", type=int, default=30,
                   help="If no --date, search this many days from today (default 30)")
    p.add_argument("--return", dest="return_date", help="Return date YYYY-MM-DD for a round trip")
    p.add_argument("--from", dest="origin", default=LONDON_AIRPORTS, help="Origin airport codes")
    p.add_argument("--to", dest="dest", help="Destination airport codes, comma-separated "
                   "(default: the region's list at the top of this file)")
    p.add_argument("--adults", type=int, default=1)
    p.add_argument("--forecast-only", action="store_true", help="Skip the API call")
    p.add_argument("--chart", action="store_true", help="Save price_chart.png (needs matplotlib)")
    args = p.parse_args()

    region = args.region
    region_name = REGIONS[region]["name"]
    dest = args.dest or REGIONS[region]["airports"]
    travel_date = args.date or (dt.date.today() + dt.timedelta(days=args.days_ahead)).isoformat()
    google_history = []

    if not args.forecast_only:
        key = os.environ.get("SERPAPI_KEY")
        if not key:
            sys.exit("Set SERPAPI_KEY first (see the top of this file).")
        row, google_history = search_all(key, args.origin, dest, travel_date,
                                         args.return_date, args.adults)
        row["region"] = region
        save_row(row)
        stops = "direct" if int(row["stops"]) == 0 else f"{row['stops']} stop(s)"
        print(f"\nCheapest London to {region_name} today ({row['checked_on']}) for {travel_date}:")
        print(f"  {CURRENCY} {row['price']}  {row['airline']}  {row['route']}  ({stops})")
        if row["typical_low"]:
            print(f"  Google's typical range: {row['typical_low']} to {row['typical_high']}"
                  f" ({row['price_level'] or 'n/a'})")

    rows = load_rows()
    points = build_points(rows, region, travel_date, args.return_date, google_history)
    if not points:
        sys.exit(f"No saved {region_name} prices for {travel_date} yet. "
                 "Run without --forecast-only first.")

    dates, series = daily_series(points)
    fc = forecast(series)
    latest = series[-1]
    typical_low = next((r["typical_low"] for r in reversed(rows)
                        if (r.get("region") or "us") == region
                        and r["travel_date"] == travel_date and r["typical_low"]), None)
    days_to_go = (dt.date.fromisoformat(travel_date) - dt.date.today()).days

    print(f"\nHistory: {len(series)} day(s), low {min(series):.0f}, high {max(series):.0f}")
    if fc:
        print(f"\nForecast (next {FORECAST_DAYS} days):")
        for i, (pt, lo, hi) in enumerate(fc, 1):
            day = dates[-1] + dt.timedelta(days=i)
            print(f"  {day:%a %d %b}  ~{pt:,.0f}   (likely {lo:,.0f} to {hi:,.0f})")
    else:
        print("\nForecast needs at least 3 days of prices — keep it running.")

    print("\n" + advise(latest, series, fc, typical_low, days_to_go) + "\n")

    if args.chart:
        save_chart(dates, series, fc,
                   os.path.join(os.path.dirname(DATA_FILE), f"price_chart_{region}.png"),
                   region_name)


if __name__ == "__main__":
    main()
