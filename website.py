#!/usr/bin/env python3
# dashboard.py
from flask import Flask, jsonify, render_template_string, send_from_directory
import csv
import json
import os
from datetime import datetime

# --- CONFIG ---
EVENT_LOG = "events_log.csv"
TRIP_LOG = "trip_log.csv"
MAX_RECENT_EVENTS = 50
MAX_RECENT_TRIPS = 100
POLL_INTERVAL_SEC = 2
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 5000
# ---------------

app = Flask(__name__)

# --- Helpers to parse logs ----------------------------------------------
def safe_load_json(s):
    try:
        return json.loads(s)
    except Exception:
        return None

def parse_event_log():
    """
    Parse events_log.csv and return a list of parsed events.
    We reconstruct semantics:
      - wifi_event: event field 'connected' -> entry, 'disconnected' -> exit
      - rfid (type:'rfid'): toggle: if uid not onboard -> entry, else -> exit
    Returns events list in chronological order and final onboard sets.
    """
    events = []
    onboard_rfid = set()
    onboard_wifi = set()

    if not os.path.exists(EVENT_LOG):
        return events, onboard_rfid, onboard_wifi

    try:
        with open(EVENT_LOG, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            # Try to detect header: if first row contains non-ISO timestamp, treat as header
            all_rows = list(reader)
            # If header exists and matches "timestamp,type,data" leave it; else proceed similarly
            for row in all_rows:
                if not row:
                    continue
                # Expect at least timestamp, type, data/json
                if len(row) < 3:
                    # Some earlier versions may have other formats; skip if unparsable
                    continue
                timestamp_str = row[0].strip()
                typ = row[1].strip()
                data_field = row[2].strip()
                # If there's a header like "timestamp,type,data", skip
                if timestamp_str.lower() in ("timestamp", "time") and typ.lower() in ("type",):
                    continue

                # parse timestamp if possible
                try:
                    ts = datetime.fromisoformat(timestamp_str)
                    ts_out = ts.isoformat()
                except Exception:
                    # fallback: keep raw
                    ts_out = timestamp_str

                # parse inner json if possible
                inner = safe_load_json(data_field)
                # some files write just a CSV with different columns: attempt to accept them
                if inner and isinstance(inner, dict):
                    # standard: inner has "type": "rfid" or "wifi_event"
                    inner_type = inner.get("type", "")
                    if inner_type == "wifi_event" or typ == "wifi_event" or "wifi" in inner_type:
                        mac = inner.get("mac") or inner.get("addr") or inner.get("mac_addr") or ""
                        ev = inner.get("event", "").lower()
                        if ev == "connected":
                            action = "entry"
                            onboard_wifi.add(mac.upper())
                        elif ev == "disconnected":
                            action = "exit"
                            onboard_wifi.discard(mac.upper())
                        else:
                            action = ev or "unknown"
                        events.append({
                            "ts": ts_out,
                            "source": "wifi",
                            "id": (mac or "").upper(),
                            "action": action,
                            "raw": inner
                        })
                    elif inner_type == "rfid" or typ == "rfid" or "rfid" in inner_type:
                        uid = inner.get("uid") or inner.get("id") or ""
                        uid = uid.upper()
                        # toggle: if not onboard -> entry else exit
                        if uid not in onboard_rfid:
                            action = "entry"
                            onboard_rfid.add(uid)
                        else:
                            action = "exit"
                            onboard_rfid.discard(uid)
                        events.append({
                            "ts": ts_out,
                            "source": "rfid",
                            "id": uid,
                            "action": action,
                            "raw": inner
                        })
                    else:
                        # Unknown structured event - push as-is
                        events.append({
                            "ts": ts_out,
                            "source": inner.get("type", typ),
                            "id": inner.get("uid") or inner.get("mac") or "",
                            "action": "unknown",
                            "raw": inner
                        })
                else:
                    # Fallback parsing if data_field is not JSON (maybe older format)
                    # Attempt to parse as: timestamp, pid, event, type
                    # e.g. 2025-11-07 14:25:00,ABCD1234,entry,rfid
                    if len(row) >= 4:
                        pid = row[1].strip()
                        ev = row[2].strip().lower()
                        ptype = row[3].strip().lower()
                        if ptype == "rfid":
                            uid = pid.upper()
                            if ev == "entry":
                                onboard_rfid.add(uid)
                            elif ev == "exit":
                                onboard_rfid.discard(uid)
                            events.append({
                                "ts": ts_out,
                                "source": "rfid",
                                "id": uid,
                                "action": ev,
                                "raw": {"pid": pid, "event": ev, "type": ptype}
                            })
                        elif ptype in ("wifi", "wifi_event"):
                            mac = pid.upper()
                            if ev == "entry" or ev == "connected":
                                onboard_wifi.add(mac)
                            elif ev == "exit" or ev == "disconnected":
                                onboard_wifi.discard(mac)
                            events.append({
                                "ts": ts_out,
                                "source": "wifi",
                                "id": mac,
                                "action": ev,
                                "raw": {"pid": pid, "event": ev, "type": ptype}
                            })
                        else:
                            # unknown, append raw
                            events.append({
                                "ts": ts_out,
                                "source": ptype,
                                "id": pid,
                                "action": ev,
                                "raw": {"row": row}
                            })
                    else:
                        # cannot parse - store as raw
                        events.append({
                            "ts": ts_out,
                            "source": typ,
                            "id": "",
                            "action": "unknown",
                            "raw": {"row": row}
                        })
    except Exception as e:
        print("Error parsing event log:", e)

    return events, onboard_rfid, onboard_wifi

def load_trips():
    """
    Load completed trips from trip_log.csv
    Expect columns: id,name,entry_time,exit_time,duration_min,fare,source
    """
    trips = []
    total_fare = 0.0
    if not os.path.exists(TRIP_LOG):
        return trips, total_fare
    try:
        with open(TRIP_LOG, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    fare = float(row.get("fare", 0) or 0)
                except Exception:
                    fare = 0.0
                total_fare += fare
                trips.append({
                    "id": row.get("id", ""),
                    "name": row.get("name", ""),
                    "entry_time": row.get("entry_time", ""),
                    "exit_time": row.get("exit_time", ""),
                    "duration_min": row.get("duration_min", ""),
                    "fare": fare,
                    "source": row.get("source", "")
                })
    except Exception as e:
        print("Error reading trips:", e)
    return trips, round(total_fare, 2)

# --- API endpoints -----------------------------------------------------
@app.route("/api/status")
def api_status():
    events, onboard_rfid, onboard_wifi = parse_event_log()
    trips, total_fare = load_trips()
    status = {
        "passengers_total": len(onboard_rfid) + len(onboard_wifi),
        "passengers_rfid": len(onboard_rfid),
        "passengers_wifi": len(onboard_wifi),
        "onboard_rfid": sorted(list(onboard_rfid)),
        "onboard_wifi": sorted(list(onboard_wifi)),
        "total_fare": total_fare,
        "last_event": events[-1] if events else None
    }
    return jsonify(status)

@app.route("/api/events")
def api_events():
    events, _, _ = parse_event_log()
    # return last N events (most recent last)
    return jsonify(events[-MAX_RECENT_EVENTS:])

@app.route("/api/trips")
def api_trips():
    trips, total_fare = load_trips()
    return jsonify({
        "total_fare": total_fare,
        "trips": trips[-MAX_RECENT_TRIPS:]
    })

# --- Frontend page -----------------------------------------------------
INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Bus Dashboard</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial; background:#0b1220; color:#e6eef8; margin:0; padding:0; }
    .wrap { max-width:1200px; margin:12px auto; padding:12px; }
    header { display:flex; align-items:center; justify-content:space-between; }
    h1 { margin:0; font-size:24px; color:#62d6ff; }
    .big { font-size:48px; color:#7efc8d; margin:6px 0; }
    .card { background: #07101a; border-radius:10px; padding:12px; box-shadow: 0 6px 18px rgba(0,0,0,0.6); }
    .grid { display:grid; grid-template-columns: 1fr 1fr; gap:12px; margin-top:12px; }
    .events, .trips { max-height:420px; overflow:auto; }
    .event-row { padding:8px; border-bottom:1px solid rgba(255,255,255,0.03); display:flex; justify-content:space-between; align-items:center;}
    .badge { padding:6px 10px; border-radius:8px; font-weight:600; }
    .entry { background: linear-gradient(90deg,#2ed573,#1fa2ff); color:#002; }
    .exit { background: linear-gradient(90deg,#ff7b7b,#ffd46b); color:#222; }
    .welcome { position: fixed; right:20px; top:20px; background:#0b2636; padding:14px 20px; border-radius:12px; box-shadow: 0 8px 30px rgba(2,10,20,0.6); display:none; z-index:999; }
    table { width:100%; border-collapse:collapse; }
    th, td { text-align:left; padding:6px 8px; border-bottom:1px solid rgba(255,255,255,0.03); }
    footer { margin-top:18px; color:#9fb7c8; font-size:13px; }
    @media (max-width:800px) { .grid { grid-template-columns: 1fr; } .big{font-size:36px} }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>🚌 Bus Dashboard</h1>
      <div>
        <div id="clock"></div>
      </div>
    </header>

    <div style="display:flex; gap:12px; margin-top:12px;">
      <div class="card" style="flex:1;">
        <div style="display:flex; align-items:center; justify-content:space-between;">
          <div>
            <div style="font-size:14px; color:#9fb7c8">Total Passengers</div>
            <div id="pass_total" class="big">0</div>
            <div style="display:flex; gap:12px; margin-top:8px;">
              <div style="font-size:13px; color:#cde8ff">RFID: <strong id="pass_rfid">0</strong></div>
              <div style="font-size:13px; color:#cde8ff">Wi-Fi: <strong id="pass_wifi">0</strong></div>
            </div>
          </div>
          <div style="text-align:right;">
            <div style="font-size:14px; color:#9fb7c8">Total Fare Collected</div>
            <div style="font-size:28px; color:#ffd166">₹ <span id="total_fare">0.00</span></div>
          </div>
        </div>
      </div>

      <div class="card" style="flex:1;">
        <div style="font-size:14px; color:#9fb7c8">Last Event</div>
        <div id="last_event_box" style="margin-top:8px;">
          <div style="font-size:18px" id="last_event_text">—</div>
          <div style="font-size:13px; color:#9fb7c8" id="last_event_ts"></div>
        </div>
      </div>
    </div>

    <div class="grid">
      <div class="card events">
        <h3 style="margin-top:0">Recent Events</h3>
        <div id="events_list"></div>
      </div>

      <div class="card trips">
        <h3 style="margin-top:0">Recent Trips</h3>
        <table>
          <thead><tr><th>Passenger</th><th>From</th><th>To</th><th>Dur (min)</th><th>Fare</th></tr></thead>
          <tbody id="trips_table"></tbody>
        </table>
      </div>
    </div>

    <footer>
      Running on Raspberry Pi — polls every {{ poll_interval }}s — CSVs: <code>{{ event_log }}</code>, <code>{{ trip_log }}</code>
    </footer>
  </div>

  <div class="welcome" id="welcome_box"></div>

<script>
let lastEventId = null;

function fmtDate(ts) {
  if (!ts) return "";
  try {
    let d = new Date(ts);
    if (isNaN(d)) return ts;
    return d.toLocaleString();
  } catch(e) { return ts; }
}

function showWelcome(text, sub) {
  const el = document.getElementById("welcome_box");
  el.innerHTML = "<div style='font-size:18px;color:#bfffb2; font-weight:700;'>" + text + "</div>" +
                 (sub ? ("<div style='margin-top:6px;color:#cfeaff;'>" + sub + "</div>") : "");
  el.style.display = "block";
  // hide after 3.5s
  setTimeout(()=>{ el.style.display = "none"; }, 3500);
}

async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    const j = await res.json();
    document.getElementById("pass_total").innerText = j.passengers_total;
    document.getElementById("pass_rfid").innerText = j.passengers_rfid;
    document.getElementById("pass_wifi").innerText = j.passengers_wifi;
    document.getElementById("total_fare").innerText = j.total_fare.toFixed(2);

    const last = j.last_event;
    if (last) {
      document.getElementById("last_event_text").innerText = (last.action?.toUpperCase() || '-') + " — " + (last.source || '') + " " + (last.id ? last.id.slice(-6) : '');
      document.getElementById("last_event_ts").innerText = fmtDate(last.ts);
    }
  } catch (e) {
    console.error("status error", e);
  }
}

async function fetchEvents() {
  try {
    const res = await fetch('/api/events');
    const arr = await res.json();
    const list = document.getElementById("events_list");
    list.innerHTML = "";
    let newestId = null;
    for (let i = arr.length - 1; i >= 0; --i) {
      const ev = arr[i];
      const row = document.createElement("div");
      row.className = "event-row";
      const left = document.createElement("div");
      left.innerHTML = `<div style="font-weight:700">${(ev.source||'').toUpperCase()} ${ev.id ? ev.id.slice(-6) : ''}</div>
                        <div style="font-size:12px;color:#9fb7c8">${fmtDate(ev.ts)}</div>`;
      const right = document.createElement("div");
      const badge = document.createElement("span");
      badge.className = 'badge ' + (ev.action === 'entry' ? 'entry' : ev.action === 'exit' ? 'exit' : '');
      badge.innerText = (ev.action || '').toUpperCase();
      right.appendChild(badge);
      row.appendChild(left);
      row.appendChild(right);
      list.appendChild(row);

      // track the latest event id (ts+id+action)
      const id = (ev.ts || '') + '|' + (ev.id||'') + '|' + (ev.action||'');
      newestId = id;
    }

    // if newest differs from lastEventId, show welcome if entry
    if (newestId && newestId !== lastEventId) {
      // find the last event
      if (arr.length > 0) {
        const ev = arr[arr.length - 1];
        if (ev.action === 'entry') {
          if (ev.source === 'rfid') {
            showWelcome('Welcome!', 'RFID ' + (ev.id ? ev.id.slice(-6) : ''));
          } else {
            showWelcome('Welcome Wi-Fi', 'MAC ' + (ev.id ? ev.id.slice(-6) : ''));
          }
        } else if (ev.action === 'exit') {
          showWelcome('Goodbye!', '');
        }
      }
      lastEventId = newestId;
    }
  } catch (e) {
    console.error("events error", e);
  }
}

async function fetchTrips() {
  try {
    const res = await fetch('/api/trips');
    const j = await res.json();
    const rows = j.trips || [];
    const tb = document.getElementById("trips_table");
    tb.innerHTML = "";
    for (let i = rows.length - 1; i >= 0; --i) {
      const t = rows[i];
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${t.name || t.id || '—'}</td>
                      <td>${t.entry_time ? new Date(t.entry_time).toLocaleTimeString() : ''}</td>
                      <td>${t.exit_time ? new Date(t.exit_time).toLocaleTimeString() : ''}</td>
                      <td>${t.duration_min}</td>
                      <td>₹${(t.fare||0).toFixed(2)}</td>`;
      tb.appendChild(tr);
    }
  } catch (e) {
    console.error("trips error", e);
  }
}

function tickClock() {
  document.getElementById("clock").innerText = new Date().toLocaleString();
}

async function refreshAll() {
  await Promise.all([fetchStatus(), fetchEvents(), fetchTrips()]);
}

setInterval(()=>{ tickClock(); }, 1000);
setInterval(()=>{ refreshAll(); }, {{ poll_interval_ms }});
window.addEventListener('load', ()=>{ refreshAll(); });

</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX_HTML,
                                  event_log=EVENT_LOG,
                                  trip_log=TRIP_LOG,
                                  poll_interval=POLL_INTERVAL_SEC,
                                  poll_interval_ms=POLL_INTERVAL_SEC * 1000)

# Static files (if any)
@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

# --- Run server ---------------------------------------------------------
if __name__ == "__main__":
    print(f"Starting dashboard on http://{LISTEN_HOST}:{LISTEN_PORT}  (poll every {POLL_INTERVAL_SEC}s)")
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False)
