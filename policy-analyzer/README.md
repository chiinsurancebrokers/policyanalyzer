# Policy Analyzer

Upload IPMI/health insurance policy documents (PDF/TXT) and get:

1. A structured, side-by-side comparison of coverage, deductibles, **excess**,
   annual limits, **area of cover**, **underwriting basis**, waiting periods,
   and clause-level **critical limitations** — the sub-limits and day caps
   buried in the wording that materially change what a claim pays out (e.g.
   a 90-day cap on mechanical ventilation/life support despite an otherwise
   unlimited inpatient benefit).
2. **HAL's recommendation**: tell it about the applicant's needs, budget,
   and priorities, and Claude recommends the best-fit plan, ranked, with
   client-facing reasoning and any critical limitations that are directly
   relevant to what this applicant said they need.
3. An optional **second opinion from GPT-4o-mini** — either reviewing
   Claude's extraction against the source text, or independently reviewing
   HAL's recommendation itself and confirming or challenging it.
4. Export to Excel, Markdown, or a **client-ready PDF report** covering the
   applicant's needs, full comparison, per-provider detail with critical
   limitations flagged, HAL's recommendation, and the second opinion.

Rewritten from an earlier prototype: consolidated six overlapping app
versions into one, replaced the flat 3-field extraction with a proper
schema, added content-hash caching (no repeat AI cost for the same
document), and uses Claude Haiku for extraction (cost-efficient, well-scoped
task) and a stronger Claude model for HAL's recommendation (needs more
reasoning over multiple plans + applicant context at once).

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY
streamlit run app.py
```

## Deploy to Railway

1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**, select this repo.
3. Railway auto-detects `railway.json` / `Procfile` and builds via Nixpacks.
4. In the Railway project's **Variables** tab, set:
   - `ANTHROPIC_API_KEY` (required for AI extraction and HAL's recommendation —
     without it, extraction falls back to a rule-based keyword scan and HAL's
     recommendation is unavailable)
   - `OPENAI_API_KEY` (optional — enables both second-opinion buttons; if unset,
     they're simply hidden)
   - Optionally override `CLAUDE_MODEL` and `HAL_MODEL` (both default to
     `claude-haiku-4-5-20251001` — see "Model choice" below for the
     cost/accuracy trade-off), `THINKING_BUDGET_TOKENS` (default 3,000),
     `GPT_MODEL`, `MAX_FILE_SIZE_MB`, `MAX_EXTRACTION_CHARS` (default 150,000 —
     the character budget sent to the AI per policy; raise it further for
     unusually long wording documents), `CACHE_ENABLED`
5. Deploy. Railway assigns a public URL automatically.

Note: `data/cache.db` (the extraction cache) lives on ephemeral local disk
by default — fine for cost-saving within a deploy, but it resets on
redeploy. For a persistent cache across deploys, attach a Railway volume
mounted at `/app/data`.

## Project layout

```
app.py                  Streamlit UI (single entrypoint)
core/
  config.py              Environment-driven settings
  extract.py              PDF/TXT text extraction + provider name detection
  ai_analyzer.py           Claude structured extraction (with caching + fallback)
  fallback_analyzer.py      Rule-based extraction, used if no API key / AI call fails
  cache.py                  SQLite cache keyed by document content hash (or hash of
                             applicant profile + document set, for recommendations)
  comparison.py              Builds the comparison table + Excel/Markdown export
  recommender.py             HAL: recommends the best-fit plan for the applicant
  pdf_report.py               Builds the client-facing PDF report (reportlab)
  gpt_verifier.py              GPT-4o-mini second opinion — on the extraction,
                                and separately on HAL's recommendation
```

## Uploading policies: per-provider document slots

Each provider you're comparing gets its own card with **three** uploaders:

- **Quotation / Policy Certificate** — short, applicant-specific: selected
  tier, premium, sums insured, the deductible/excess actually chosen.
- **Policy Wording / Member Guide** — the full terms and conditions:
  exclusions, underwriting basis, area of cover, waiting periods,
  clause-level critical limitations.
- **Supporting documents** (optional) — riders, endorsements, benefit
  tables, correspondence.

All files in a provider's card are merged into ONE combined document before
extraction — the quotation's specific figures take priority, the wording
fills in everything not restated in the quotation, and if they disagree on
a figure the quotation wins (noted in the summary). This avoids the old
failure mode of a wording book and its matching quote being treated as two
separate, incomplete policies. Click **➕ Add another provider** for each
additional plan you're comparing.

## The applicant-needs -> recommendation flow

1. Fill in the **Applicant needs** section (ages/dependents, residence, area
   of cover needed, budget, known conditions, priorities, free-text notes).
2. Click **Analyze & Compare** — runs the structured extraction once per
   provider, over that provider's merged documents.
3. Click **Get HAL's recommendation** — HAL reasons only over the structured
   extractions already produced (not the raw documents again), so it can't
   contradict them and stays cheap regardless of document length. It returns
   a ranked recommendation, plain-language reasoning, key trade-offs, and any
   critical limitations specifically relevant to what this applicant said
   they need.
4. Optionally click **Get ChatGPT's second opinion on HAL's pick** — GPT-4o-mini
   independently reviews the same applicant needs and extractions plus HAL's
   recommendation, and returns agree/partial/disagree with its own reasoning
   and an alternative if it disagrees.
5. Export — Excel/Markdown as before, or the new **PDF Report**, which bundles
   the applicant profile, comparison table, per-provider detail with critical
   limitations highlighted, HAL's recommendation, and the second opinion into
   one client-ready document.

Both the recommendation and its second opinion are cached by a hash of the
applicant profile + the set of document content hashes, so re-generating a
PDF or re-visiting the same case doesn't trigger repeat paid calls.

## Chat with HAL

Below HAL's recommendation, a chat lets you ask follow-up questions grounded
in everything analyzed so far — the applicant profile, every provider's full
extraction, and HAL's current recommendation if generated. HAL answers only
from that data and says so explicitly if something isn't covered, rather
than inventing figures. This is plain conversation (not a structured-JSON
call), and it's not cached — each question gets a fresh answer with the
full case context.

**Adding a document mid-conversation:** the "📎 Add a document to the
conversation" panel lets you attach another file — an addendum, a missed
supporting document, or an entirely new provider — without restarting the
analysis. Choose an existing provider (it merges in and re-analyzes that
policy) or "+ New provider" (it's analyzed and added to the comparison).
Either way, HAL's existing recommendation is cleared automatically, since
the underlying data just changed — re-run it from the button above, or ask
about the change directly in the chat.

Mentioning a new fact about the applicant in the chat itself (e.g. a medical
condition) only affects that chat turn's answer — it won't carry into a
future "Get HAL's recommendation" re-run unless you also update the
Applicant Needs form.

## Model choice: Haiku 4.5 + extended thinking

Both extraction and HAL's recommendation default to Claude Haiku 4.5 —
Anthropic's fastest and cheapest current model, at roughly a fifth of Opus
4.8's per-token cost. Since extraction runs once per policy over up to
`MAX_EXTRACTION_CHARS` of document text (150,000 by default), model choice
is the single biggest lever on cost, and Haiku keeps that affordable even
at high volume.

To offset Haiku's lighter default reasoning, both calls run with **extended
thinking** enabled (`THINKING_BUDGET_TOKENS`, default 3,000) — Haiku 4.5 is
the first Haiku model to support it. This gives the model room to reason
through tier/column matching and hunt for clause-level critical limitations
before answering, without moving to a pricier model tier. Note that the
`effort` parameter (available on Opus and Sonnet-tier models) is **not**
supported on Haiku 4.5 — extended thinking is the equivalent lever for this
model.

`CLAUDE_MODEL` (extraction) and `HAL_MODEL` (recommendation) are
independently configurable via env var if accuracy on a specific case
matters more than cost — e.g. `HAL_MODEL=claude-opus-4-8` to run only the
final recommendation on the strongest available model while keeping
extraction on Haiku for volume.

## GPT-4o-mini second opinion

Same verification pattern used in the Asklepios/KiraAIpet apps: rather than
extracting independently and hoping the two results line up, GPT-4o-mini is
shown the source document **and** Claude's extraction, and asked to check
it — flag discrepancies, note anything missed, and give an agree/partial/
disagree verdict. This catches extraction errors more reliably than two
blind extractions that might just disagree without either being clearly
right.

It's opt-in per document (a button in each provider's expander), not run
automatically on every upload, since it's an extra paid call. If
`OPENAI_API_KEY` isn't set, the button is simply hidden and the app runs on
Claude alone. Verification results are cached the same way extractions are
— same document won't be re-verified twice.
