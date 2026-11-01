"""Run the local claims demo: python app.py [--host 127.0.0.1] [--port 8000]."""
from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from workflow import ClaimsService

ROOT = Path(__file__).resolve().parent
PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claims Desk</title><style>
:root{font:15px system-ui;color:#172538;background:#f2f5f8}body{margin:0}header{background:#183b56;color:white;padding:20px 5vw}main{max-width:1200px;margin:24px auto;padding:0 20px}.grid{display:grid;grid-template-columns:2fr 1fr;gap:18px}.card{background:white;border:1px solid #d9e1e8;border-radius:10px;padding:18px;margin-bottom:16px}h1{font-size:23px;margin:0}h2{font-size:18px;margin:0 0 14px}label{display:block;font-size:13px;color:#536477;margin:10px 0 4px}input,select,button{font:inherit;padding:9px;border:1px solid #bac8d4;border-radius:6px}input,select{width:100%;box-sizing:border-box}button{background:#175c82;color:white;border:0;cursor:pointer;margin:8px 5px 0 0}button:hover{background:#0d4565}.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;padding:8px;border-bottom:1px solid #e3e9ee}tr[data-id]{cursor:pointer}tr[data-id]:hover{background:#edf6fb}.muted{color:#66788a;font-size:13px}.pill{background:#e4f1f8;padding:3px 8px;border-radius:20px}.events{max-height:240px;overflow:auto}.notice{padding:10px;background:#edf6fb;border-radius:6px;margin:8px 0}.error{background:#fff0ed;color:#8f281c}.metrics{display:flex;gap:8px;flex-wrap:wrap}.metric{background:#eef4f7;padding:10px 12px;border-radius:8px}.hidden{display:none}@media(max-width:800px){.grid{grid-template-columns:1fr}}
</style></head><body><header><h1>Insurance Claims Desk</h1><div class="muted" style="color:#d4e7f3">Intake · coverage decisions · evidence · assessment · approval · payment audit</div></header>
<main><div id="notice" class="notice hidden"></div><section class="card"><h2>Operations metrics</h2><div id="metrics" class="metrics">Loading…</div></section>
<div class="grid"><div><section class="card"><h2>Claims queue</h2><div id="claims"></div></section><section class="card"><h2>New claim intake</h2><label>Matching in-force policy and incident</label><select id="reference"><option>Loading valid examples…</option></select><div class="row"><div><label>Claim amount (USD)</label><input id="amount" type="number" min="1" value="2500"></div><div><label>Actor role</label><select id="role"><option value="requester">Business requester</option><option value="operations_analyst">Operations analyst</option><option value="approver">Approver</option><option value="process_owner">Process owner</option><option value="system_administrator">System administrator</option></select></div></div><button onclick="intake()">Submit claim</button><span class="muted">The examples have valid policy and coverage dates for the incident.</span></section></div>
<div><section class="card"><h2>Selected claim</h2><div id="detail" class="muted">Select a claim in the queue.</div><div id="actions" class="hidden"><label>Action role</label><select id="actionRole"><option value="operations_analyst">Operations analyst</option><option value="requester">Business requester</option><option value="approver">Approver</option><option value="system_administrator">System administrator</option></select><div id="actionButtons"></div></div></section></div></div></main>
<script>
let selected=null;const $=id=>document.getElementById(id);function msg(t,bad=false){$('notice').textContent=t;$('notice').className='notice'+(bad?' error':'');setTimeout(()=>$('notice').className='notice hidden',6000)}
async function api(url,method='GET',body){const role=$('actionRole').value;let r=await fetch(url,{method,headers:{'Content-Type':'application/json','X-Role':role,'X-Actor':'demo-user'},body:body?JSON.stringify(body):undefined});let d=await r.json();if(!r.ok)throw Error(d.error||r.statusText);return d}
async function refresh(){let [cs,m]=await Promise.all([api('/api/claims'),api('/api/metrics')]);$('claims').innerHTML='<table><thead><tr><th>Claim</th><th>Type</th><th>Amount</th><th>Status</th></tr></thead><tbody>'+cs.map(c=>`<tr data-id="${c.claim_id}" onclick="showClaim('${c.claim_id}')"><td>${c.claim_id}</td><td>${c.claim_type}</td><td>$${Number(c.claim_amount).toFixed(2)}</td><td><span class="pill">${c.status}</span></td></tr>`).join('')+'</tbody></table>';
$('metrics').innerHTML=[['Claims',m.total_claims],['Documents complete',Math.round(m.document_completeness_rate*100)+'%'],['Paid settlements',m.paid_settlements],['Within-limit payments',Math.round(m.settlement_reconciliation_rate*100)+'%'],['Audit events',m.audit_events]].map(x=>`<div class="metric"><b>${x[1]}</b><br><span class="muted">${x[0]}</span></div>`).join('');if(selected)await showClaim(selected)}
async function showClaim(id){selected=id;let c=await api('/api/claims/'+encodeURIComponent(id));$('actions').className='';$('detail').innerHTML=`<b>${c.claim_id}</b> · ${c.claim_type} · $${Number(c.claim_amount).toFixed(2)}<p>Status: <span class="pill">${c.status}</span><br>Reason: ${c.denial_reason||'In review'}<br>Eligible: ${c.eligible_amount==null?'—':'$'+Number(c.eligible_amount).toFixed(2)}</p><b>Documents</b><ul>${c.documents.map(d=>`<li>${d.document_type}: ${d.status}</li>`).join('')||'<li>None</li>'}</ul><b>Decision and audit trail</b><div class="events">${c.claim_events.map(e=>`<div class="notice"><b>${e.action}</b> · ${e.actor_role} (${e.actor_id})<br>${e.reason}<br><span class="muted">${e.created_at}</span></div>`).join('')||'No events yet'}</div>`;
let b='';if(c.status==='PENDING_DOCUMENTS')b+='<button onclick="receiveDoc()">Mark requested documents received</button>';if(['UNDER_REVIEW','PENDING_DOCUMENTS'].includes(c.status))b+='<button onclick="assess()">Assess eligibility</button>';if(c.status==='APPROVAL_PENDING')b+='<button onclick="approve()">Approve settlement</button><button onclick="reject()">Reject</button>';if(c.status==='SETTLEMENT_APPROVED')b+='<button onclick="pay()">Record payment (admin)</button>';b+='<button onclick="assign()">Assign specialist adjuster</button>';if(c.status==='PENDING_DOCUMENTS')b+='<button onclick="requestDocs()">Request missing documents</button>';$('actionButtons').innerHTML=b}
async function act(fn){try{await fn();msg('Action saved with an audit event.');await refresh()}catch(e){msg(e.message,true)}}
function intake(){act(async()=>{$('actionRole').value=$('role').value;let [policy_id,incident_id]=$('reference').value.split('|||');let d=await api('/api/claims','POST',{policy_id,incident_id,claim_amount:Number($('amount').value),claim_type:null});selected=d.claim_id})}
function receiveDoc(){act(async()=>api(`/api/claims/${selected}/documents/receive`,'POST',{}))}function requestDocs(){act(async()=>api(`/api/claims/${selected}/documents/request`,'POST',{}))}function assess(){act(async()=>api(`/api/claims/${selected}/assess`,'POST',{}))}function approve(){act(async()=>api(`/api/claims/${selected}/approve`,'POST',{decision:'APPROVE'}))}function reject(){act(async()=>api(`/api/claims/${selected}/approve`,'POST',{decision:'REJECT'}))}function pay(){act(async()=>api(`/api/claims/${selected}/pay`,'POST',{}))}function assign(){act(async()=>api(`/api/claims/${selected}/assign`,'POST',{}))}
async function loadReferences(){let rs=await api('/api/reference');$('reference').innerHTML=rs.map(r=>`<option value="${r.policy_id}|||${r.incident_id}">${r.product_type} · ${r.policy_id} · ${r.incident_id}</option>`).join('')||'<option>No valid examples available</option>'}
Promise.all([refresh(),loadReferences()]).catch(e=>msg(e.message,true));
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    service: ClaimsService

    def log_message(self, fmt: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def _json(self, status: int, data: object) -> None:
        raw = json.dumps(data, default=str).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def _body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(size) or b"{}")

    def _identity(self) -> tuple[str, str]:
        return self.headers.get("X-Role", "requester"), self.headers.get("X-Actor", "demo-user")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            raw = PAGE.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if path == "/api/claims": return self._json(200, self.service.list_claims())
        if path == "/api/metrics": return self._json(200, self.service.metrics())
        if path == "/api/rules": return self._json(200, self.service.rules())
        if path == "/api/reference": return self._json(200, self.service.reference_pairs())
        match = re.fullmatch(r"/api/claims/([^/]+)", path)
        if match:
            try: return self._json(200, self.service.get_claim(match.group(1)))
            except KeyError as e: return self._json(404, {"error": str(e)})
        return self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path; data = self._body(); role, actor = self._identity()
        try:
            if path == "/api/claims":
                result = self.service.intake(data["policy_id"], data["incident_id"], float(data["claim_amount"]), role, actor, data.get("claim_type"))
            else:
                m = re.fullmatch(r"/api/claims/([^/]+)/(documents/request|documents/receive|assess|assign|approve|pay)", path)
                if not m: return self._json(404, {"error": "Not found"})
                cid, action = m.groups()
                if action == "documents/request":
                    result = self.service.request_documents(cid, role, actor)
                elif action == "documents/receive":
                    result = self.service.get_claim(cid)
                    pending = [d["document_type"] for d in result["documents"] if d["status"] != "RECEIVED"]
                    if not pending: raise ValueError("No outstanding document request")
                    for doc_type in pending: result = self.service.receive_document(cid, doc_type, role, actor)
                elif action == "assess": result = self.service.assess(cid, role, actor)
                elif action == "assign":
                    row = self.service._claim(cid)
                    candidates = self.service.db.execute("SELECT adjuster_id,specializations FROM adjusters WHERE status='ACTIVE' ORDER BY adjuster_id").fetchall()
                    match = next((r for r in candidates if row["claim_type"] in r["specializations"].upper()), None)
                    if not match: raise ValueError("No active specialist adjuster available")
                    result = self.service.assign_adjuster(cid, data.get("adjuster_id", match["adjuster_id"]), role, actor)
                elif action == "approve": result = self.service.approve(cid, data.get("decision", "APPROVE"), role, actor)
                else: result = self.service.pay(cid, role, actor)
            return self._json(200, result)
        except PermissionError as e: return self._json(403, {"error": str(e)})
        except KeyError as e: return self._json(404, {"error": str(e)})
        except (ValueError, TypeError, json.JSONDecodeError) as e: return self._json(400, {"error": str(e)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local insurance claims workflow")
    parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=str(ROOT / "output")); parser.add_argument("--db", default=str(ROOT / "workflow.sqlite"))
    args = parser.parse_args()
    service = ClaimsService(args.data_dir, args.db); Handler.service = service
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Claims Desk is ready at http://{args.host}:{args.port} (Ctrl+C to stop)")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close(); service.close()


if __name__ == "__main__": main()
