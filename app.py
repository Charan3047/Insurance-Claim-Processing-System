"""Run the local claims workbench: python app.py [--host 127.0.0.1] [--port 8000]."""
from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from workflow import ClaimsService

ROOT = Path(__file__).resolve().parent
PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Insurance Claims Desk — Advanced Workbench v2.0</title><style>
:root{font:14px/1.45 system-ui,-apple-system,sans-serif;color:#0f172a;background:#f1f5f9}
body{margin:0}header{background:#0f2942;color:#fff;padding:18px 4vw;display:flex;justify-content:space-between;align-items:center}
main{max-width:1360px;margin:20px auto;padding:0 20px}.grid{display:grid;grid-template-columns:1.35fr 1fr;gap:18px}
.card{background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:16px;margin-bottom:16px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
h1{font-size:21px;margin:0}h2{font-size:16px;margin:0 0 12px;color:#0f2942;border-bottom:1px solid #e2e8f0;padding-bottom:8px}
label{display:block;font-size:12px;font-weight:600;color:#475569;margin:8px 0 4px}
input,select,button{font:inherit;padding:8px 10px;border:1px solid #cbd5e1;border-radius:6px}
input,select{width:100%;box-sizing:border-box}
button{background:#0284c7;color:#fff;border:0;cursor:pointer;margin:6px 6px 0 0;font-weight:600;font-size:13px}
button:hover{background:#0369a1}button.warn{background:#b45309}button.danger{background:#b91c1c}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.filters{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px}
table{border-collapse:collapse;width:100%;font-size:12.5px}td,th{text-align:left;padding:7px 8px;border-bottom:1px solid #e2e8f0}
th{background:#f8fafc;color:#334155;font-weight:600}tr[data-id]{cursor:pointer}tr[data-id]:hover{background:#e0f2fe}
.muted{color:#64748b;font-size:12.5px}.pill{background:#e0f2fe;color:#0369a1;padding:2px 8px;border-radius:99px;font-weight:600;font-size:11.5px}
.events{max-height:260px;overflow:auto}.notice{padding:9px 12px;background:#f0f9ff;border-left:3px solid #0284c7;border-radius:6px;margin:6px 0;font-size:12.5px}
.error{background:#fef2f2;border-left-color:#dc2626;color:#991b1b}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.metric{background:#f8fafc;border:1px solid #e2e8f0;padding:10px 12px;border-radius:8px}.metric b{font-size:18px;color:#0f2942}
.hidden{display:none}.subtable{margin:8px 0 12px}@media(max-width:920px){.grid{grid-template-columns:1fr}}
</style></head><body>
<header><div><h1>Insurance Claims Desk — Advanced Workbench v2.0</h1><div class="muted" style="color:#bae6fd">14-Table Relational Model · Bottom-Up Line-Item Reconciliation · N:M Bridges · SIU Exception Routing · Full Audit Trail</div></div></header>
<main><div id="notice" class="notice hidden"></div>
<section class="card"><h2>Live Portfolio & Reconciliation Metrics</h2><div id="metrics" class="metrics">Loading…</div></section>
<div class="grid"><div>
<section class="card"><h2>Claims Queue (1,000 Seeded Portfolio Claims)</h2>
<div class="filters">
<select id="filterStatus" onchange="refresh()"><option value="">All Statuses</option><option>SUBMITTED</option><option>PENDING_DOCUMENTS</option><option>UNDER_REVIEW</option><option>SIU_INVESTIGATION</option><option>APPROVED</option><option>SETTLED</option><option>CLOSED</option><option>DENIED</option></select>
<select id="filterType" onchange="refresh()"><option value="">All Product Types</option><option>AUTO</option><option>HOME</option><option>HEALTH</option><option>LIFE</option><option>TRAVEL</option></select>
<button onclick="refresh()" style="margin:0">Refresh Queue</button>
</div>
<div id="claims" style="max-height:380px;overflow:auto"></div></section>
<section class="card"><h2>New Claim Intake (Role-Checked)</h2>
<label>Matching in-force policy and incident</label><select id="reference"><option>Loading valid examples…</option></select>
<div class="row"><div><label>Claim amount (USD)</label><input id="amount" type="number" min="1" value="2500"></div>
<div><label>Actor role</label><select id="role"><option value="requester">Business requester</option><option value="operations_analyst">Operations analyst</option><option value="approver">Approver</option><option value="process_owner">Process owner</option><option value="system_administrator">System administrator</option></select></div></div>
<button onclick="intake()">Submit claim</button><span class="muted">Validates policy dates, status, and coverage exclusions automatically.</span></section>
</div>
<div><section class="card"><h2>Selected Claim 360° Inspector</h2>
<div id="detail" class="muted">Select any claim in the queue to inspect line items, N:M adjuster/coverage bridges, documents, and audit history.</div>
<div id="actions" class="hidden"><label>Active Actor Role</label>
<select id="actionRole"><option value="operations_analyst">Operations analyst</option><option value="requester">Business requester</option><option value="approver">Approver</option><option value="process_owner">Process owner</option><option value="system_administrator">System administrator</option></select>
<div id="actionButtons"></div></div></section></div></div></main>
<script>
let selected=null;const $=id=>document.getElementById(id);
function msg(t,bad=false){$('notice').textContent=t;$('notice').className='notice'+(bad?' error':'');setTimeout(()=>$('notice').className='notice hidden',6000)}
async function api(url,method='GET',body){const role=$('actionRole').value;let r=await fetch(url,{method,headers:{'Content-Type':'application/json','X-Role':role,'X-Actor':'workbench-user'},body:body?JSON.stringify(body):undefined});let d=await r.json();if(!r.ok)throw Error(d.error||r.statusText);return d}
async function refresh(){
  const st=$('filterStatus').value,tp=$('filterType').value;
  const q=new URLSearchParams();if(st)q.set('status',st);if(tp)q.set('claim_type',tp);
  let [cs,m]=await Promise.all([api('/api/claims?'+q.toString()),api('/api/metrics')]);
  $('claims').innerHTML='<table><thead><tr><th>Claim ID</th><th>Product</th><th>Claimed</th><th>Priority</th><th>Status</th></tr></thead><tbody>'+cs.map(c=>`<tr data-id="${c.claim_id}" onclick="showClaim('${c.claim_id}')"><td><b>${c.claim_id}</b></td><td>${c.claim_type}</td><td>$${Number(c.claim_amount).toLocaleString('en-US',{minimumFractionDigits:2})}</td><td>${c.priority||'MEDIUM'}</td><td><span class="pill">${c.status}</span></td></tr>`).join('')+'</tbody></table>';
  $('metrics').innerHTML=[['Total Claims',m.total_claims],['Docs Complete',Math.round(m.document_completeness_rate*100)+'%'],['Paid Settlements',m.paid_settlements],['Within-Limit Rate',Math.round(m.settlement_reconciliation_rate*100)+'%'],['N:M Adjuster Links',m.adjuster_bridge_assignments],['N:M Coverage Links',m.coverage_bridge_allocations],['Audit Events',m.audit_events]].map(x=>`<div class="metric"><b>${x[1]}</b><br><span class="muted">${x[0]}</span></div>`).join('');
  if(selected)await showClaim(selected);
}
async function showClaim(id){
  selected=id;let c=await api('/api/claims/'+encodeURIComponent(id));$('actions').className='';
  let itemsHtml=(c.claim_items||[]).map(i=>`<tr><td>${i.item_description}</td><td>${i.quantity} × $${Number(i.unit_cost).toFixed(2)}</td><td>$${Number(i.claimed_amount).toFixed(2)}</td><td>${i.approved_amount==null?'—':'$'+Number(i.approved_amount).toFixed(2)}</td><td>${i.status}</td></tr>`).join('');
  let bridgesHtml=(c.claim_adjuster_assignments||[]).map(a=>`<li><b>${a.adjuster_id}</b>: ${a.assignment_role} (weight ${a.workload_weight})</li>`).join('');
  $('detail').innerHTML=`<b>${c.claim_id}</b> · ${c.claim_type} · Claimed: <b>$${Number(c.claim_amount).toFixed(2)}</b> · Policy: <code>${c.policy_id}</code>
  <p>Status: <span class="pill">${c.status}</span> · Fraud Flag: <b>${c.fraud_indicator?'YES (SIU)':'No'}</b><br>Denial/Review Note: ${c.denial_reason||'Active workflow'}<br>Eligible Amount: <b>${c.eligible_amount==null?'—':'$'+Number(c.eligible_amount).toFixed(2)}</b></p>
  <b>Line Items (Bottom-Up Derived)</b><table class="subtable"><thead><tr><th>Item</th><th>Qty × Unit</th><th>Claimed</th><th>Approved</th><th>State</th></tr></thead><tbody>${itemsHtml}</tbody></table>
  <b>N:M Adjuster Assignments</b><ul>${bridgesHtml||'<li>None</li>'}</ul>
  <b>Mandatory Documents</b><ul>${c.documents.map(d=>`<li>${d.document_type}: <b>${d.status}</b></li>`).join('')||'<li>None</li>'}</ul>
  <b>Immutable Decision & Audit Trail (${c.claim_events.length} events)</b><div class="events">${c.claim_events.map(e=>`<div class="notice"><b>${e.action}</b> · ${e.actor_role} (${e.actor_id})<br>${e.reason}<br><span class="muted">${e.created_at}</span></div>`).join('')||'No events yet'}</div>`;
  let b='';
  if(c.status==='PENDING_DOCUMENTS'){b+='<button onclick="receiveDoc()">Mark requested documents received</button><button onclick="requestDocs()">Request missing documents</button>';}
  if(['UNDER_REVIEW','PENDING_DOCUMENTS','SIU_INVESTIGATION'].includes(c.status)){b+='<button onclick="assess()">Assess eligibility</button><button class="warn" onclick="escalateSiu()">Escalate to SIU</button>';}
  if(['APPROVAL_PENDING','UNDER_REVIEW'].includes(c.status)){b+='<button onclick="approve()">Approve settlement</button><button class="danger" onclick="reject()">Reject</button>';}
  if(['SETTLEMENT_APPROVED','APPROVED'].includes(c.status))b+='<button onclick="pay()">Record payment (admin)</button>';
  b+='<button onclick="assign()">Assign specialist adjuster</button>';
  $('actionButtons').innerHTML=b;
}
async function act(fn){try{await fn();msg('Action saved and appended to audit log.');await refresh()}catch(e){msg(e.message,true)}}
function intake(){act(async()=>{$('actionRole').value=$('role').value;let [policy_id,incident_id]=$('reference').value.split('|||');let d=await api('/api/claims','POST',{policy_id,incident_id,claim_amount:Number($('amount').value),claim_type:null});selected=d.claim_id})}
function receiveDoc(){act(async()=>api(`/api/claims/${selected}/documents/receive`,'POST',{}))}
function requestDocs(){act(async()=>api(`/api/claims/${selected}/documents/request`,'POST',{}))}
function assess(){act(async()=>api(`/api/claims/${selected}/assess`,'POST',{}))}
function approve(){act(async()=>api(`/api/claims/${selected}/approve`,'POST',{decision:'APPROVE'}))}
function reject(){act(async()=>api(`/api/claims/${selected}/approve`,'POST',{decision:'REJECT'}))}
function pay(){act(async()=>api(`/api/claims/${selected}/pay`,'POST',{}))}
function assign(){act(async()=>api(`/api/claims/${selected}/assign`,'POST',{}))}
function escalateSiu(){act(async()=>api(`/api/claims/${selected}/siu`,'POST',{reason:'Flagged for SIU review from workbench'}))}
async function loadReferences(){let rs=await api('/api/reference');$('reference').innerHTML=rs.map(r=>`<option value="${r.policy_id}|||${r.incident_id}">${r.product_type} · ${r.policy_id} · ${r.incident_id}</option>`).join('')||'<option>No valid examples available</option>'}
Promise.all([refresh(),loadReferences()]).catch(e=>msg(e.message,true));
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    service: ClaimsService

    def log_message(self, fmt: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def _json(self, status: int, data: object) -> None:
        raw = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(size) or b"{}")

    def _identity(self) -> tuple[str, str]:
        return self.headers.get("X-Role", "requester"), self.headers.get("X-Actor", "demo-user")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == "/":
            raw = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/api/claims":
            st = qs.get("status", [None])[0]
            tp = qs.get("claim_type", [None])[0]
            return self._json(200, self.service.list_claims(status=st, claim_type=tp))
        if path == "/api/metrics":
            return self._json(200, self.service.metrics())
        if path == "/api/rules":
            return self._json(200, self.service.rules())
        if path == "/api/reference":
            return self._json(200, self.service.reference_pairs())
        match = re.fullmatch(r"/api/claims/([^/]+)", path)
        if match:
            try:
                return self._json(200, self.service.get_claim(match.group(1)))
            except KeyError as e:
                return self._json(404, {"error": str(e)})
        return self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        data = self._body()
        role, actor = self._identity()
        try:
            if path == "/api/claims":
                result = self.service.intake(
                    data["policy_id"], data["incident_id"], float(data["claim_amount"]),
                    role, actor, data.get("claim_type"),
                )
            else:
                m = re.fullmatch(
                    r"/api/claims/([^/]+)/(documents/request|documents/receive|assess|assign|approve|pay|siu)",
                    path,
                )
                if not m:
                    return self._json(404, {"error": "Not found"})
                cid, action = m.groups()
                if action == "documents/request":
                    result = self.service.request_documents(cid, role, actor)
                elif action == "documents/receive":
                    result = self.service.get_claim(cid)
                    pending = [d["document_type"] for d in result["documents"] if d["status"] != "RECEIVED"]
                    if not pending:
                        raise ValueError("No outstanding document request")
                    for doc_type in pending:
                        result = self.service.receive_document(cid, doc_type, role, actor)
                elif action == "assess":
                    result = self.service.assess(cid, role, actor)
                elif action == "assign":
                    row = self.service._claim(cid)
                    candidates = self.service.db.execute(
                        "SELECT adjuster_id,specializations FROM adjusters WHERE status='ACTIVE' ORDER BY adjuster_id"
                    ).fetchall()
                    match = next(
                        (r for r in candidates if row["claim_type"] in r["specializations"].upper()),
                        None,
                    )
                    if not match:
                        raise ValueError("No active specialist adjuster available")
                    result = self.service.assign_adjuster(
                        cid, data.get("adjuster_id", match["adjuster_id"]),
                        role, actor, data.get("assignment_role", "PRIMARY_ADJUSTER"),
                    )
                elif action == "siu":
                    result = self.service.escalate_siu(
                        cid, data.get("reason", "Suspected fraud escalated to SIU"), role, actor
                    )
                elif action == "approve":
                    result = self.service.approve(cid, data.get("decision", "APPROVE"), role, actor)
                else:
                    result = self.service.pay(cid, role, actor)
            return self._json(200, result)
        except PermissionError as e:
            return self._json(403, {"error": str(e)})
        except KeyError as e:
            return self._json(404, {"error": str(e)})
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            return self._json(400, {"error": str(e)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local insurance claims workflow")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=str(ROOT / "output"))
    parser.add_argument("--db", default=str(ROOT / "workflow.sqlite"))
    args = parser.parse_args()
    service = ClaimsService(args.data_dir, args.db)
    Handler.service = service
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Claims Desk is ready at http://{args.host}:{args.port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()


if __name__ == "__main__":
    main()
