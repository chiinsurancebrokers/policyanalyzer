from __future__ import annotations

import hmac, json, os
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from core.ai_analyzer import analyze_with_ai
from core.grouping import GroupDoc, combine_documents, combined_hash

app = FastAPI(title='Ashlar Policy Analyzer API', version='1.0')

class AnalyzeRequest(BaseModel):
    provider_label: str = Field(default='Current policy', max_length=160)
    target_plan: str | None = Field(default=None, max_length=240)
    quotation_text: str = Field(default='', max_length=120000)
    wording_text: str = Field(default='', max_length=180000)
    supporting_text: str = Field(default='', max_length=120000)
    benefit_rows: list[dict] = Field(default_factory=list, max_length=120)

def require_bridge_key(x_hal_policy_analyzer_key: str | None = Header(default=None)) -> None:
    expected = os.getenv('HAL_POLICY_ANALYZER_API_KEY', '').strip()
    if not expected or not x_hal_policy_analyzer_key or not hmac.compare_digest(expected, x_hal_policy_analyzer_key):
        raise HTTPException(status_code=401, detail='Invalid Policy Analyzer bridge key')

@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'policy-analyzer-api'}

@app.post('/api/v1/analyze', dependencies=[Depends(require_bridge_key)])
def analyze(req: AnalyzeRequest):
    docs = []
    if req.quotation_text.strip(): docs.append(GroupDoc('current-policy-certificate.txt', req.quotation_text, 'quotation'))
    if req.wording_text.strip(): docs.append(GroupDoc('provider-policy-wording.txt', req.wording_text, 'wording'))
    if req.supporting_text.strip(): docs.append(GroupDoc('provider-supporting-document.txt', req.supporting_text, 'supporting'))
    if not docs: raise HTTPException(status_code=400, detail='No policy text supplied.')
    merged = combine_documents(docs)
    result = analyze_with_ai(merged, combined_hash(docs))
    rows = []
    if req.benefit_rows and os.getenv('ANTHROPIC_API_KEY','').strip():
        try:
            import anthropic
            from core.claude_utils import get_response_text
            schema = [{'code': str(x.get('code',''))[:100], 'label': str(x.get('label',''))[:500]} for x in req.benefit_rows[:120]]
            prompt = (
                'Extract each requested benefit row from this current IPMI policy. '
                'Certificate/quotation controls selected options; policy wording controls contractual scope and exclusions. '
                'Never infer one benefit from a related category. Return ONLY JSON with key benefit_rows. '
                'Each row must contain code, status (confirmed|not_covered|optional_not_confirmed|not_found), value, evidence.\n\n'
                + 'TARGET: ' + (req.target_plan or 'Current policy') + '\nPROVIDER: ' + req.provider_label
                + '\nREQUESTED ROWS:\n' + json.dumps(schema, ensure_ascii=False)
                + '\nPOLICY EVIDENCE:\n' + merged[:150000]
            )
            client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
            response = client.messages.create(model=os.getenv('CLAUDE_MODEL','claude-haiku-4-5-20251001'), max_tokens=9000, messages=[{'role':'user','content':prompt}])
            raw = get_response_text(response).strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and isinstance(parsed.get('benefit_rows'), list): rows = parsed['benefit_rows']
        except Exception as exc:
            result.setdefault('bridge_warnings', []).append('Detailed benefit-row extraction unavailable: ' + type(exc).__name__)
    result['hal_benefit_rows'] = rows
    result['provider_label'] = req.provider_label
    result['target_plan'] = req.target_plan
    return result
