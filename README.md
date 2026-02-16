# EPL 2025-26 Season Simulator

Interactive web app that lets you simulate the remaining Premier League matches for **Aston Villa**, **Manchester United**, **Chelsea**, and **Liverpool** — and see who qualifies for the Champions League.

## How It Works

- Pick a matchweek (27–38) and predict each fixture: home win, draw, or away win
- The standings table updates instantly after every selection
- Arsenal and Man City are assumed to finish 1st and 2nd — the four focus teams compete for positions 3rd through 6th
- Top 5 qualify for the Champions League, so one focus team will miss out
- Use **Simulate All Remaining** for a random scenario, or **Reset All** to start over

## Quick Start

Open `index.html` in any browser. No build step, no dependencies, no API keys needed.

To serve locally:

```bash
python3 -m http.server 8080
# Open http://localhost:8080
```

## Tech Stack

- Single self-contained HTML file (HTML + CSS + JS)
- No frameworks or external dependencies
- Standings data baked in from [football-data.org](https://www.football-data.org) as of Feb 16, 2026
- Premier League themed design with responsive layout

## Features

- W/D/L prediction buttons with clear team labels
- Live-updating 4-team standings table with UCL qualification line
- 12 matchweeks of fixtures (42 total matches)
- Head-to-head match detection between focus teams
- Simulate This Week / Simulate All / Reset controls
- Champions League verdict banner when all matches are complete
- "How This Works" explainer modal
- Mobile-responsive layout
