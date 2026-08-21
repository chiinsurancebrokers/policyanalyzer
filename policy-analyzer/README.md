# Policy Analyzer

Upload IPMI/health insurance policy documents (PDF/TXT), get a structured
side-by-side comparison of coverage, deductibles, limits, and exclusions.

Rewritten from an earlier prototype: consolidated six overlapping app
versions into one, replaced the flat 3-field extraction with a proper
schema, added content-hash caching (no repeat AI cost for the same
document), and switched to Claude Haiku as the sole AI backend for
extraction-quality-per-dollar.

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
   - `ANTHROPIC_API_KEY` (required for AI extraction — without it, the app
     falls back to a rule-based keyword scan)
   - Optionally override `CLAUDE_MODEL`, `MAX_FILE_SIZE_MB`, `CACHE_ENABLED`
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
  cache.py                  SQLite cache keyed by document content hash
  comparison.py              Builds the comparison table + Excel/Markdown export
```

## Why Claude Haiku for extraction

The original project called both GPT-4 and Claude Sonnet/Haiku with
inconsistent model strings (several were already deprecated). Structured
field extraction from a policy document is a well-scoped task that doesn't
need a frontier model — Haiku gives comparable extraction quality at a
fraction of the cost.

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
