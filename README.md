# London flight tracker: USA and Europe

Two tools that share one price history:

- `fare_tracker.html` is a manual tracker with separate **USA** and **Europe** tabs, each with its own prices, forecast and signal. Open it in any browser, log the cheapest fare you see each day, and it charts the history and forecasts the next 7 days. It works offline and keeps data in that browser only. Bookmark `fare_tracker.html#usa` or `fare_tracker.html#europe` to open straight to a tab.
- `flight_tracker.py` is an automatic checker. It looks up the cheapest fare to the USA or Europe once a day, saves it to `prices.csv` (with a region column, so each keeps its own history), and prints a forecast with a book / wait signal. You can import `prices.csv` into the HTML app to see it charted.

## Setting up the automatic script

1. Install Python 3.9 or newer. Nothing else is required (`pip install matplotlib` if you want `--chart`).
2. Sign up at serpapi.com and copy your API key. Their free plan includes a limited number of searches per month; one run uses one search, so once a day fits easily.
3. Save the key:
   - macOS / Linux: `export SERPAPI_KEY="your_key"` (add it to `~/.zshrc` or `~/.bashrc` to keep it)
   - Windows: `setx SERPAPI_KEY "your_key"`, then open a new terminal
4. Test it:
   ```
   python flight_tracker.py --date 2027-03-14                   # USA
   python flight_tracker.py --region europe --date 2027-03-14   # Europe
   ```

### Useful options

| Option | What it does |
|---|---|
| `--region europe` | Check London to Europe instead of the USA (default `us`) |
| `--date 2027-03-14` | Track one specific travel date (best for forecasting) |
| `--days-ahead 45` | Instead of a fixed date, always search 45 days from today |
| `--return 2027-03-28` | Round trip instead of one way |
| `--to JFK,EWR` | Only these airports (defaults: 7 major US airports, or 7 popular European ones such as Paris, Barcelona, Rome and Lisbon; edit `REGIONS` at the top of the script to change them). Google Flights allows 7 airports per search, so a longer list is split automatically and uses one search per group of 7. |
| `--from LHR` | Only Heathrow (default is all five London airports) |
| `--adults 2` | Price for two people |
| `--chart` | Save `price_chart.png` |
| `--forecast-only` | Re-run the forecast without using a search |

## Running it every day

macOS / Linux, using cron (`crontab -e`), runs at 8am:
```
0 8 * * * SERPAPI_KEY="your_key" /usr/bin/python3 /path/to/flight_tracker.py --date 2027-03-14 >> /path/to/tracker.log 2>&1
5 8 * * * SERPAPI_KEY="your_key" /usr/bin/python3 /path/to/flight_tracker.py --region europe --date 2027-03-14 >> /path/to/tracker.log 2>&1
```

Tracking both regions uses two searches a day (about 60 a month), so check that your SerpApi plan allows that.

Windows: open Task Scheduler, choose Create Basic Task, set it to Daily, and for the action use
`python` as the program and `C:\path\to\flight_tracker.py --date 2027-03-14` as the arguments. Make a second task with `--region europe` added for Europe.

## How the forecast works

Both tools use Holt's exponential smoothing: it tracks the current price level and its trend, and projects that trend forward a week with a widening likely range. The script also seeds the model with Google Flights' own recent price history for your trip, so it gives a forecast from day one; the HTML app needs 3 days of logged prices.

Treat it as a guide rather than a guarantee. Airline pricing reacts to sales, demand and seat availability that no trend line can see. The book / wait signal also considers how close you are to departure, since fares usually climb in the final three weeks.
