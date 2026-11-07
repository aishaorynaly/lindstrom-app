# Lindström Customer Review & Value Generator

**Live app:** [lindstrom-app.onrender.com](https://lindstrom-app.onrender.com/) (ask the owner for the password)

A decision-support tool for Key Account Managers (KAMs): a multi-site value dashboard, an AI assistant on the right, and an editable PowerPoint for steering meetings. It is **not** audited compliance or CSRD reporting, and all data in `data.xlsx` is synthetic.

There are two interfaces on the same data:

| | What it is | Start |
|---|---|---|
| **KAM review** (main) | The review design (`portal/review.html`) with Gemini answering any question about the data set, at http://127.0.0.1:8765 on your own computer | `python server.py` |
| Earlier KAM portal | The nine evidence areas, What-if, Signals and KAM inputs (`portal/index.html`), at `/index.html` on the same server | `python server.py` |
| Streamlit dashboard | The minimal overview with progressive disclosure, and an isolated focused view for every question to the AI assistant | `streamlit run app.py` |

## Run the portal

```bash
python -m venv .venv --system-site-packages
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe server.py
```

Open http://127.0.0.1:8765. To enable the Gemini assistant, set `GEMINI_API_KEY` (a Google AI Studio key) in the terminal that starts the server. The key stays on the server; the browser never sees it. Without a key (or when `portal/index.html` is opened directly) the portal still works in offline-rules mode: view changes by keyword rules and the most common records by pattern.

## What the portal shows

The nine evidence areas the customer cares about, each with a green / amber / red status, a headline, the evidence lines and a provenance tag on every line:

Customer Success Scorecard, Customer Experience, Service Performance, Service Adoption, Operational Value, Financial Value, Sustainability Value, Improvement, Relationship / Future.

Tabs: Overview, Evidence areas, Sites, Service & hygiene, Circularity & cost, Environmental impact, **What-if** (baseline and team-assumption sliders), **Signals** (allocated garments with no scan activity, hygiene flag, repair vs retirement), **KAM inputs**, Data & method, Actions & export (PowerPoint, PDF, CSV).

## Put it online

GitHub stores the code but does not run it. `render.yaml` sets the app up on [Render](https://render.com):

1. In Render choose **New**, then **Blueprint**, and connect this repository.
2. Enter `GEMINI_API_KEY` and a strong `APP_PASSWORD` when asked. They stay in Render, never in the code.
3. Open the address Render gives you and sign in. Any user name works; only the password is checked.

The server refuses to start on a public host without `APP_PASSWORD`, because every question spends your Gemini quota. On Render's free plan the service sleeps when unused, so the first visit can take about a minute.

## The review design with Gemini

`portal/review.html` is the review design unchanged except for two hooks. The adjustments are in `portal/review.css` (navy palette, Inter, borders, details accordion, focused view) and `portal/review_ai.js` (the Gemini adapter and the focused view).

- **Overview.** The KPI cards show first. The other cards are the same, behind "Click to view details".
- **Questions.** Each question goes to `/api/ask`. Gemini calls the tools in `tools.py`, which compute from the whole workbook for this client: any period, site, country, product or product category, repair type, delivery stage, hygiene and fleet signals, baselines, assumptions and data quality. It also sees the CRM record and what the KAM captured. It writes words; the figures come from the tools.
- **Focused view.** Each question gets its own mini-dashboard: 2 to 3 KPI cards, one chart (bars, donut or percentage meter, whichever suits the question) and 2 to 3 AI insights ending in an executive takeaway. After Gemini answers, a second Gemini call picks the KPIs and chart from the data that answered the question (`narrative.focus_canvas`), and `checked_canvas` keeps only values that trace to that data. If that leaves it thin, `filled_canvas` fills it from the table the assistant queried. There are no topic templates, so an unrelated topic cannot appear. A question with nothing to chart, such as a contact's name, shows text cards and insights only. `← Return to Overview` restores the dashboard. A figure in the answer that cannot be traced is marked "Check before quoting".
- **Pricing and what-if questions** open their interactive views, because those have live controls. Offline, questions about a named topic use the rule-based view for that topic, and other questions say that Gemini is not connected.
- **Statements stay as they are.** "CSAT is 4.4 out of 5" still goes to the capture flow, and "Only show last quarter" still changes the dashboard.
- **What-if.** This replaces Data and method in the menu. The baseline, baseline factor scale, replacements per repair, price change and annual spend are adjustable, and every figure recalculates. Assumptions are shown beside it because they are team assumptions, not measured results.
- **Without a key** the page uses its offline rules and rule-based insights.

## The Streamlit dashboard

`app.py` follows the portal's visual design: Inter, navy `#00205b` / `#0a1b3d`, white cards on `#f4f6f8`, `1px solid #e2e8f0` borders. The theme is in `.streamlit/config.toml` and `static/theme.css`, and the font and logo are served from `static/` so the app works offline.

- **Overview.** Four KPI cards. Everything else (monthly savings, service figures, sites, drill-down, how to read the figures) is in collapsed accordions. The filters, audience and What-if assumptions are in the sidebar, collapsed.
- **Focused view.** Any question to the Insights panel replaces the overview in place with one canvas: a simple visual, risk and impact indicators, and 2 to 3 insights. `← Return to Overview` brings the overview back. The ↗ on a KPI card opens the same view without calling Gemini.
- **Which view opens.** Gemini returns a `topic` with its parameters; keyword rules in `focus.py` cover the offline case. Each topic has its own builder in `focus.py`, which uses engine figures only.
- **AI insights.** Gemini writes the 2 to 3 bullets from the view's figures. If a bullet has a figure that does not trace to the data, or the what-if or filters change afterwards, the view shows its rule-based insights instead and says so.
- **Pricing what-if.** The workbook has synthetic garment purchase costs but no contract or service prices. The view applies the percentage to those purchase costs. The service cost impact appears only when the KAM enters the annual contract value, which is labelled as a KAM input.
- **Outlook indicators** compare the last three months with the three before. They show a direction to discuss, not a forecast. "Reach 180 days within 30 days" counts traced garments that would be flagged next if no new scan is logged.
- **Filters changed by the assistant stay** after returning to the overview, labelled "AI Custom Filter", with `Reset to default view` beside them.

## Adding information from the conversation

The KAM can tell the assistant something new during the meeting, for example "CSAT from the December survey is 4.3 out of 5", "Oulu had 3 late deliveries in November", "Contract value is €420k a year" or "Action: review jackets at Helsinki, owner Anna, by 28 Feb".

1. The assistant proposes a record. **Nothing changes until the KAM confirms.**
2. On confirmation the record enters the dataset as an overlay, the affected evidence areas recalculate and flash, and every affected figure is labelled `[Estimated] KAM input` (customer-reported or KAM-reported, not verified).
3. Records are stored in the browser, can be exported and imported as JSON, and can be removed under **KAM inputs**, which restores the original figures.

Supported records: customer feedback (CSAT, NPS), service incidents, late deliveries, volume changes (wearers or allocated garments), contract value, actions, issues, opportunities, customer priorities. Operational events need a site and a month in 2025, because the data pack is monthly and ends in December 2025.

## How the AI assistant stays honest

- Gemini never writes a number. It calls tools (`set_view`, `get_metric`, `rank_sites`, `get_evidence_areas`, `get_signals`, `get_crm`, `record_inputs`) that run on the portal's own calculation layer, and the page shows which data it queried.
- Every figure in an answer is checked against the tool results; a figure that is not there is flagged.
- Gemini's parameters are validated by the portal (unknown sites, months outside the data, out-of-range scores are refused with a reason), so a wrong guess can change the view but never a number.
- Mandatory wording is enforced in the system prompt and by tests: "Allocated garments with no scan activity" is an optimization signal for investigation; wearers are counted once per site; the hygiene flag is a low-confidence screen; provenance tags are `[Measured]`, `[Calculated]`, `[Estimated]`, `[TEAM_ASSUMPTION]`.

## Files

| File | Role |
|---|---|
| `portal/review.html`, `portal/review.css`, `portal/review_ai.js` | The review design, its adjustments, and the Gemini adapter |
| `portal/index.html` | The earlier portal |
| `portal/kam.js` | KAM inputs and dataset overlays, evidence areas, What-if, Signals, KAM inputs tab, exports |
| `portal/kam_ai.js` | Assistant: Gemini engine and tools, offline rules, confirmation cards, self-checks |
| `portal/kam.css`, `portal/extra_data.js` | Styles; asset-level hygiene facts generated by `build_extra.py` |
| `server.py` | Serves the portal, answers questions through Gemini (key stays server-side), and checks the password |
| `render.yaml` | Deployment settings for Render |
| `llm.py` | Gemini client wrapper: key, model, error handling |
| `config.py`, `loader.py`, `engine.py`, `narrative.py`, `tools.py`, `charts.py`, `exporter.py` | Python engine |
| `app.py`, `focus.py` | The Streamlit dashboard, and its focused views |
| `static/` | Streamlit theme CSS, Inter font files and logo |

## Tests

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest tests -q
```

The browser tests (`tests/test_portal.py`) drive the real portal in Edge or Chrome, served by `server.py` with a fake Gemini behind it, and are skipped where no browser is installed. They check that the portal's figures equal the Python engine's for every customer, period and scope, that records change exactly the figures they should, that the key never reaches the page, and the layout, tabs and exports.

## Known data limits

- **The hygiene flag is a low-confidence screen.** The cycle log traces about 6% of allocated hygiene garments, and the longest gap between two recorded scans is 170 days, so every flag over 180 days comes from the log ending. The portal shows the coverage beside the flag.
- **Full TCO cannot be calculated** unless the KAM adds the contract value.
- **Repair frequency is by site and garment type, not by garment** (`Repairs` has no asset ID).
- **Repair savings assume 0.5 to 1 replacement postponed per repair** (workbook assumption A002); the What-if tab shows the range.
- **"Last month" is December 2025** and "last 30 days" cannot be shown exactly: the data is monthly.
