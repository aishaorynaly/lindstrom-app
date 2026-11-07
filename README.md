# Lindström Customer Review & Value Generator

**Live app:** [lindstrom-app.onrender.com](https://lindstrom-app.onrender.com/) (ask the owner for the password)

A decision-support tool for Key Account Managers (KAMs). It combines a multi-site value dashboard, an AI assistant and an exportable review for steering meetings. It is **not** audited compliance or CSRD reporting, and all data in `data.xlsx` is synthetic.

## Open the app

Open the [live app](https://lindstrom-app.onrender.com/) and sign in. Any user name works; only the password is checked. On Render's free plan the app sleeps when unused, so the first visit can take about a minute.

| Interface | What it is | Where |
|---|---|---|
| **KAM review** (main) | The review design, with Gemini answering questions about the data as a mini-dashboard | [Open the live app](https://lindstrom-app.onrender.com/) |
| Earlier KAM portal | The nine evidence areas, What-if, Signals and KAM inputs | [Open the earlier portal](https://lindstrom-app.onrender.com/index.html) |
| Streamlit dashboard | The first redesign | Not online. Run `streamlit run app.py` on your own computer |

## What the KAM review does

- **Overview.** The KPI cards show first. The other cards are one click away, behind "Click to view details".
- **Questions.** Ask about any period, site, country, product, repair type, delivery stage, hygiene or fleet signal, baseline or assumption. Gemini looks the answer up in the workbook for that client. It also sees the CRM record and what the KAM has captured.
- **Mini-dashboard.** Each question opens its own view with 2 to 3 KPI cards, one chart (bars, donut or percentage meter) and 2 to 3 AI insights ending in an executive takeaway. `← Return to Overview` goes back to the dashboard. A question with nothing to chart, such as a contact's name, shows text cards and insights only.
- **What-if.** This replaces Data and method in the menu. The baseline, baseline factor scale, replacements per repair, price change and annual spend are adjustable, and every figure recalculates. These are team assumptions, not measured results.
- **Pricing questions** open the interactive pricing view. The data has no contract or service prices, so the service cost impact appears only when the KAM enters the annual spend.
- **New information.** Statements such as "CSAT is 4.4 out of 5" are captured, not answered. "Only show last quarter" changes the dashboard.
- **Without Gemini** the app uses its offline rules.

## How the AI assistant stays honest

- Gemini writes the words. The figures come from data tools (`get_kpis`, `get_site_table`, `get_monthly_series`, `get_breakdown`, `get_signals`, `get_reference`) that calculate from the workbook, and the page shows which data it queried.
- Every figure in an answer is checked against the data. A figure that is not found is marked "Check before quoting".
- KPI cards and charts only show values that trace to the data. A chart with one value that cannot be traced is dropped.
- Unknown sites, months outside the data and other bad requests are refused with a reason.
- Mandatory wording is enforced in the assistant's instructions and by tests: "Allocated garments with no scan activity" is an optimization signal for investigation; wearers are counted once per site; the hygiene flag is a low-confidence screen.

## Hosting

GitHub stores the code but does not run it. The app runs on [Render](https://render.com), using the settings in `render.yaml`.

- `GEMINI_API_KEY` and `APP_PASSWORD` are set in Render, never in the code.
- The server refuses to start on a public host without `APP_PASSWORD`, because every question spends the Gemini quota.
- Pushing to `main` normally redeploys the app.
- If a Gemini model is busy or retired, the app retries and moves to another model the key can use. Set `GEMINI_MODEL` to choose one yourself.

## Run it on your own computer

You only need this to develop the app. Everyone else can use the live link.

1. Install it (first time only):

```bash
python -m venv .venv --system-site-packages
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

2. Set your own Gemini key from [Google AI Studio](https://aistudio.google.com/apikey) in PowerShell, before starting the server and in the same window:

```powershell
$k = Read-Host "Gemini API key" -AsSecureString; $env:GEMINI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($k))
```

3. Start the server:

```bash
.venv/Scripts/python.exe server.py
```

4. Open the address it prints in your browser. It should also say `Gemini assistant: ON`. That address only works on your computer, while the server is running.

## Files

| File | Role |
|---|---|
| `portal/review.html`, `portal/review.css`, `portal/review_ai.js` | The KAM review: the design, its adjustments, and the Gemini connection |
| `portal/index.html`, `portal/kam.js`, `portal/kam_ai.js`, `portal/kam.css` | The earlier portal |
| `portal/extra_data.js` | Asset-level hygiene facts generated by `build_extra.py` |
| `server.py` | Serves the app, answers questions through Gemini, and checks the password |
| `llm.py` | Gemini access: key, model choice, retries and error handling |
| `config.py`, `loader.py`, `engine.py`, `narrative.py`, `tools.py`, `charts.py`, `exporter.py` | Calculations, assistant text and data tools |
| `app.py`, `focus.py`, `static/` | The Streamlit dashboard |
| `render.yaml` | Hosting settings for Render |
| `data.xlsx` | The synthetic data pack |

## Tests

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest tests -q
```

The browser tests run in Edge or Chrome with a fake Gemini, and are skipped where neither is installed.

## Known data limits

- **The hygiene flag is a low-confidence screen.** The cycle log traces about 6% of allocated hygiene garments, and every flag over 180 days comes from the log ending. The coverage is shown beside the flag.
- **Full TCO cannot be calculated** because the data has no contract or service prices.
- **Repair frequency is by site and garment type, not by garment.** `Repairs` has no asset ID.
- **Repair savings assume 0.5 to 1 replacement postponed per repair** (workbook assumption A002).
- **"Last month" is December 2025**, and "last 30 days" cannot be shown exactly because the data is monthly.
