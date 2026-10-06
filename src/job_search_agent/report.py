from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path


def _rejection_category(reason: str) -> str:
    lowered = reason.casefold()
    if "outside the configured preferred locations" in lowered:
        return "Location outside preferences"
    if "years of experience" in lowered or "required experience" in lowered:
        return "Experience requirement"
    if "title indicates" in lowered:
        return "Seniority in title"
    if "internship" in lowered:
        return "Internship preference"
    if "excluded" in lowered:
        return "Explicit exclusion"
    return "Other"


def generate_html_report(input_path: Path, output_path: Path, *, threshold: float) -> dict[str, int]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    raw_matches = payload.get("all_jobs")
    if not isinstance(raw_matches, list):
        raise ValueError(f"{input_path} does not contain an all_jobs list")

    rows = []
    rejection_counts: Counter[str] = Counter()
    for match in raw_matches:
        job = match.get("job", {})
        reason = str(match.get("hard_rejection_reason") or "")
        score = float(match.get("total_score") or 0)
        if reason:
            status = "rejected"
            rejection_counts[_rejection_category(reason)] += 1
        elif match.get("shortlisted"):
            status = "shortlisted"
        elif score >= max(0, threshold - 10):
            status = "near-match"
        else:
            status = "eligible"
        rows.append(
            {
                "status": status,
                "score": score,
                "title": str(job.get("title") or ""),
                "company": str(job.get("company") or ""),
                "location": str(job.get("location") or "Unknown"),
                "work_mode": str(job.get("work_mode") or "unknown"),
                "employment_type": str(job.get("employment_type") or ""),
                "profile": str(match.get("profile_name") or ""),
                "url": str(job.get("url") or ""),
                "reason": reason,
                "rationale": str(match.get("rationale") or ""),
            }
        )
    rows.sort(key=lambda item: (-item["score"], item["title"].casefold()))
    counts = Counter(row["status"] for row in rows)
    counts["total"] = len(rows)
    scan_id = payload.get("scan_id", "unknown")
    generated_at = str(payload.get("generated_at") or "")
    warning_items = "".join(
        f"<li>{html.escape(str(warning))}</li>" for warning in payload.get("warnings", [])
    )
    scan_notice = (
        '<section class="panel warn"><h2>Partial scan — results are incomplete</h2>'
        '<p>Some pages or job details could not be verified. This is not the complete job list.</p>'
        f'<details><summary>Scan warnings</summary><ul>{warning_items}</ul></details></section>'
        if payload.get("scan_status") == "partial" else ""
    )
    serialized_rows = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    rejection_html = "".join(
        f"<li><span>{html.escape(label)}</span><strong>{count:,}</strong></li>"
        for label, count in rejection_counts.most_common()
    ) or "<li><span>No hard rejections</span><strong>0</strong></li>"
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job scan {html.escape(str(scan_id))} report</title>
<style>
:root{{color-scheme:dark;--bg:#0b1120;--panel:#141e30;--ink:#e6edf7;--muted:#a8b5c9;--line:#30405a;--brand:#93b4ff;--good:#6ee7b7;--warn:#fcd34d;--bad:#fda4af;--field:#0f1829;--header:#1c2a41;--hover:#1b2940}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif}}
main{{max-width:1500px;margin:auto;padding:28px}} h1{{margin:0 0 4px;font-size:28px}} .muted{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:12px;margin:22px 0}}
.card,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 2px 8px #0003}}
.card{{padding:16px}} .card strong{{display:block;font-size:25px;margin-top:4px}} .good{{color:var(--good)}} .warn{{color:var(--warn)}} .bad{{color:var(--bad)}}
.layout{{display:grid;grid-template-columns:280px 1fr;gap:16px}} .panel{{padding:16px}} ul{{padding:0;margin:10px 0;list-style:none}} li{{display:flex;justify-content:space-between;gap:8px;padding:7px 0;border-bottom:1px solid var(--line)}}
.toolbar{{display:grid;grid-template-columns:minmax(220px,1fr) 180px 150px 120px;gap:10px;margin-bottom:12px}}
input,select{{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--field);color:var(--ink)}} input::placeholder{{color:var(--muted);opacity:1}} :focus-visible{{outline:2px solid var(--brand);outline-offset:3px}} ::selection{{background:#3157a0;color:#fff}}
.table-wrap{{overflow:auto;max-height:72vh;border:1px solid var(--line);border-radius:9px}} table{{width:100%;border-collapse:collapse;min-width:1050px}}
th,td{{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}} th{{position:sticky;top:0;background:var(--header);cursor:pointer;z-index:1}} tr:hover{{background:var(--hover)}} a{{color:var(--brand);font-weight:600;text-decoration:none}} a:hover{{text-decoration:underline}}
.pill{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:#293951;color:var(--ink)}} .pill.shortlisted{{background:#113e32;color:var(--good)}} .pill.near-match{{background:#49391a;color:var(--warn)}} .pill.rejected{{background:#4c2431;color:var(--bad)}}
.detail{{max-width:360px;color:var(--muted);font-size:12px}} @media(max-width:900px){{.cards{{grid-template-columns:repeat(2,1fr)}}.layout{{grid-template-columns:1fr}}.toolbar{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main>
<h1>Job scan {html.escape(str(scan_id))}</h1><div class="muted">Generated {html.escape(generated_at)} · shortlist threshold {threshold:g}</div>
{scan_notice}
<section class="cards">
<div class="card">All jobs<strong>{counts['total']:,}</strong></div>
<div class="card good">Shortlisted<strong>{counts['shortlisted']:,}</strong></div>
<div class="card warn">Near matches<strong>{counts['near-match']:,}</strong></div>
<div class="card">Eligible below range<strong>{counts['eligible']:,}</strong></div>
<div class="card bad">Hard rejected<strong>{counts['rejected']:,}</strong></div>
</section>
<section class="layout"><aside class="panel"><h2>Rejection summary</h2><ul>{rejection_html}</ul></aside>
<div class="panel"><div class="toolbar">
<input id="search" type="search" placeholder="Search title, company, location…">
<select id="status"><option value="">All statuses</option><option value="shortlisted">Shortlisted</option><option value="near-match">Near matches</option><option value="eligible">Eligible</option><option value="rejected">Hard rejected</option></select>
<select id="mode"><option value="">All work modes</option><option>remote</option><option>hybrid</option><option>onsite</option><option>unknown</option></select>
<input id="minimum" type="number" min="0" max="100" value="0" aria-label="Minimum score" title="Minimum score">
</div><div id="visible" class="muted"></div><div class="table-wrap"><table><thead><tr>
<th data-key="score">Score</th><th data-key="status">Status</th><th data-key="title">Job</th><th data-key="company">Company</th><th data-key="location">Location</th><th data-key="work_mode">Mode</th><th data-key="profile">Resume</th><th>Reason / rationale</th><th>Link</th>
</tr></thead><tbody id="rows"></tbody></table></div></div></section>
</main><script id="job-data" type="application/json">{serialized_rows}</script><script>
const data=JSON.parse(document.getElementById('job-data').textContent);let sortKey='score',ascending=false;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function render(){{const q=document.getElementById('search').value.toLowerCase(),status=document.getElementById('status').value,mode=document.getElementById('mode').value,min=Number(document.getElementById('minimum').value||0);let filtered=data.filter(r=>(!status||r.status===status)&&(!mode||r.work_mode===mode)&&r.score>=min&&[r.title,r.company,r.location,r.profile,r.reason].join(' ').toLowerCase().includes(q));filtered.sort((a,b)=>{{let x=a[sortKey],y=b[sortKey];if(typeof x==='string'){{x=x.toLowerCase();y=String(y).toLowerCase()}}return(x<y?-1:x>y?1:0)*(ascending?1:-1)}});document.getElementById('visible').textContent=`Showing ${{filtered.length.toLocaleString()}} of ${{data.length.toLocaleString()}} jobs`;document.getElementById('rows').innerHTML=filtered.map(r=>`<tr><td>${{r.score.toFixed(1)}}</td><td><span class="pill ${{esc(r.status)}}">${{esc(r.status)}}</span></td><td>${{esc(r.title)}}</td><td>${{esc(r.company)}}</td><td>${{esc(r.location)}}</td><td>${{esc(r.work_mode)}}</td><td>${{esc(r.profile)}}</td><td class="detail">${{esc(r.reason||r.rationale)}}</td><td><a href="${{esc(r.url)}}" target="_blank" rel="noopener">Apply ↗</a></td></tr>`).join('')}}
document.querySelectorAll('input,select').forEach(e=>e.addEventListener('input',render));document.querySelectorAll('th[data-key]').forEach(h=>h.addEventListener('click',()=>{{if(sortKey===h.dataset.key)ascending=!ascending;else{{sortKey=h.dataset.key;ascending=true}}render()}}));render();
</script></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    return dict(counts)
