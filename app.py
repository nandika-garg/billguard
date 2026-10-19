from flask import Flask, jsonify, Response, request
import boto3, json, os, hmac, datetime

REGION = "us-east-1"
BUCKET = "billguard-nandika-0110"

s3 = boto3.client("s3", region_name=REGION)
app = Flask(__name__)

USER = "admin"
PASSWORD = os.environ.get("BILLGUARD_PASSWORD", "")


@app.before_request
def require_login():
    auth = request.authorization
    ok = (PASSWORD != "" and auth is not None
          and hmac.compare_digest(auth.username, USER)
          and hmac.compare_digest(auth.password or "", PASSWORD))
    if not ok:
        return Response("Login required", 401,
                        {"WWW-Authenticate": 'Basic realm="BillGuard"'})


def load_snapshots(limit=40):
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix="snapshots/"):
        keys += [o["Key"] for o in page.get("Contents", [])]
    snaps = []
    for k in sorted(keys)[-limit:]:
        body = s3.get_object(Bucket=BUCKET, Key=k)["Body"].read()
        snaps.append(json.loads(body))
    return snaps


def load_last_alert():
    try:
        body = s3.get_object(Bucket=BUCKET, Key="alerts/latest.json")["Body"].read()
        return json.loads(body)
    except Exception:
        return None


def load_month_cost():
    key = "cost/" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m") + ".json"
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except Exception:
        return None


@app.route("/api/data")
def data():
    snaps = load_snapshots()
    series = [{
        "t": s["timestamp"],
        "instances": len(s["instances"]),
        "s3_kb": round(s["s3"]["bytes"] / 1024, 1),
        "wasted": len(s["unattached_volumes"]) + len(s["unused_elastic_ips"]),
    } for s in snaps]
    return jsonify({
        "series": series,
        "latest": snaps[-1] if snaps else None,
        "last_alert": load_last_alert(),
        "month_cost": load_month_cost(),
    })


PAGE = '''<!doctype html>
<html><head><meta charset="utf-8"><title>BillGuard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
 body{font-family:system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#222}
 h1{margin-bottom:4px} .sub{color:#666;margin-bottom:20px}
 .cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
 .card{flex:1;min-width:150px;border:1px solid #ddd;border-radius:10px;padding:14px}
 .card b{display:block;font-size:26px}
 .ok{background:#e8f6ec;border:1px solid #9fd3ad;padding:12px;border-radius:10px}
 .bad{background:#fdeaea;border:1px solid #e39a9a;padding:12px;border-radius:10px}
 canvas{margin:16px 0} table{border-collapse:collapse;width:100%}
 td,th{border-bottom:1px solid #eee;padding:6px;text-align:left;font-size:14px}
</style></head><body>
<h1>BillGuard</h1>
<div class="sub" id="updated">Loading...</div>
<div id="status"></div>
<div class="cards">
 <div class="card"><span>Running instances</span><b id="c_inst">-</b></div>
 <div class="card"><span>Snapshot storage (KB)</span><b id="c_s3">-</b></div>
 <div class="card"><span>Wasted resources</span><b id="c_waste">-</b></div>
<div class="card"><span>Snapshots taken</span><b id="c_count">-</b></div>
 <div class="card"><span>Est. cost this month</span><b id="c_cost">-</b></div>
 <div class="card"><span>Waste cost / month</span><b id="c_wcost">-</b></div>
</div>
<h3>Running instances over time</h3><canvas id="ch1" height="90"></canvas>
<h3>Snapshot storage over time (KB)</h3><canvas id="ch2" height="90"></canvas>
<h3>Running instances now</h3>
<table id="tbl"><tr><th>ID</th><th>Type</th><th>Launched</th></tr></table>
<h3>Last alert sent</h3><div id="alert">None yet</div>
<script>
let ch1, ch2;
function mk(id, label, color){
  return new Chart(document.getElementById(id), {type:'line',
    data:{labels:[],datasets:[{label:label,data:[],borderColor:color,tension:0.2}]},
    options:{scales:{y:{beginAtZero:true}}}});
}
async function load(){
  const d = await (await fetch('/api/data')).json();
  const labels = d.series.map(s => s.t.slice(5,16).replace('T',' '));
  if(!ch1){ ch1 = mk('ch1','Instances','#2563eb'); ch2 = mk('ch2','KB','#16a34a'); }
  ch1.data.labels = labels; ch1.data.datasets[0].data = d.series.map(s => s.instances); ch1.update();
  ch2.data.labels = labels; ch2.data.datasets[0].data = d.series.map(s => s.s3_kb); ch2.update();
  const L = d.latest;
  if(!L){ document.getElementById('updated').textContent = 'No snapshots yet'; return; }
  document.getElementById('updated').textContent = 'Latest snapshot: ' + L.timestamp;
  document.getElementById('c_inst').textContent = L.instances.length;
  document.getElementById('c_s3').textContent = (L.s3.bytes/1024).toFixed(1);
  const waste = L.unattached_volumes.length + L.unused_elastic_ips.length;
  document.getElementById('c_waste').textContent = waste;
document.getElementById('c_count').textContent = d.series.length;
  const mc = d.month_cost;
  document.getElementById('c_cost').textContent = mc ? '$' + mc.total_usd.toFixed(2) : 'n/a';
  document.getElementById('c_wcost').textContent = L.est ? '$' + L.est.waste_monthly_usd.toFixed(2) : 'n/a';
  const st = document.getElementById('status');
  if(waste === 0){ st.className='ok'; st.textContent='No wasted resources found in the latest snapshot.'; }
  else {
    st.className='bad';
    st.innerHTML = '<b>Wasted resources:</b><br>' +
      L.unattached_volumes.map(v => 'Unattached volume ' + v.id + ' (' + v.size_gb + ' GB)').join('<br>') +
      (L.unattached_volumes.length && L.unused_elastic_ips.length ? '<br>' : '') +
      L.unused_elastic_ips.map(ip => 'Unused Elastic IP ' + ip).join('<br>');
  }
  const tbl = document.getElementById('tbl');
  tbl.innerHTML = '<tr><th>ID</th><th>Type</th><th>Launched</th></tr>' +
    L.instances.map(i => '<tr><td>'+i.id+'</td><td>'+i.type+'</td><td>'+i.launched.slice(0,16).replace('T',' ')+'</td></tr>').join('');
  const A = d.last_alert;
  document.getElementById('alert').innerHTML = A
    ? '<b>' + A.snapshot + '</b><br>' + A.problems.join('<br>') : 'None yet';
}
load(); setInterval(load, 60000);
</script></body></html>'''


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
