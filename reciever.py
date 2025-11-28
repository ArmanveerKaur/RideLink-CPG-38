import serial
import time
import json
import csv
import os
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# ---------- FIREBASE SETUP ----------
# a service account key file named `serviceAccountKey.json`
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()
# ------------------------------------

# ---------- CONFIG ----------
SERIAL_PORT = "/dev/ttyUSB0"   # change if needed
BAUDRATE = 115200

EVENTS_LOG = "events_log.csv"
RFID_DB = "rfid_db.csv"
TRIP_LOG = "trip_log.csv"

# Fare calculation parameters
FARE_BASE = 10            # ₹10 base fare
FARE_BASE_MINUTES = 5     # base duration in minutes
FARE_PER_MIN = 2          # ₹ per additional minute
# ----------------------------

def load_rfid_db():
    mapping = {}
    if not os.path.exists(RFID_DB):
        return mapping
    with open(RFID_DB, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row: continue
            if row[0].lower() in ('uid', 'id'):  # skip header
                continue
            uid = row[0].strip().upper()
            name = row[1].strip() if len(row) > 1 else ""
            mapping[uid] = name
    return mapping

def log_event(obj):
    # Log to CSV
    header_needed = not os.path.exists(EVENTS_LOG)
    with open(EVENTS_LOG, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if header_needed:
            writer.writerow(["timestamp", "type", "data"])
        writer.writerow([datetime.now().isoformat(), obj.get("type", ""), json.dumps(obj)])

    # Log to Firebase
    db.collection('events').add({
        'timestamp': datetime.now(),
        'type': obj.get("type", ""),
        'data': obj
    })

def log_trip(id_str, name, entry_time, exit_time, duration_min, fare, source):
    # Log to CSV
    header_needed = not os.path.exists(TRIP_LOG)
    with open(TRIP_LOG, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if header_needed:
            writer.writerow(["id", "name", "entry_time", "exit_time", "duration_min", "fare", "source"])
        writer.writerow([id_str, name, entry_time, exit_time, duration_min, fare, source])

    # Log to Firebase
    db.collection('trips').add({
        'id': id_str,
        'name': name,
        'entry_time': entry_time,
        'exit_time': exit_time,
        'duration_min': duration_min,
        'fare': fare,
        'source': source
    })

def update_passenger_count(rfid_count, wifi_count):
    total_passengers = rfid_count + wifi_count
    db.collection('passenger_count').document('current').set({
        'total': total_passengers,
        'rfid': rfid_count,
        'wifi': wifi_count,
        'timestamp': datetime.now()
    })

def calculate_fare(duration_min):
    if duration_min <= FARE_BASE_MINUTES:
        return FARE_BASE
    extra = max(0, duration_min - FARE_BASE_MINUTES)
    return FARE_BASE + extra * FARE_PER_MIN

def main():
    mapping = load_rfid_db()

    # States
    onboard_rfid = {}   # {uid: entry_time}
    onboard_wifi = {}   # {mac: entry_time}

    print("RFID mapping loaded:", mapping)
    print("Opening serial:", SERIAL_PORT)
    ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1)

    try:
        while True:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            log_event(obj)
            msg_type = obj.get("type")
            now = datetime.now()

            # ----------------- WIFI EVENTS -----------------
            if msg_type == "wifi_event":
                mac = obj.get("mac", "").upper()
                event = obj.get("event")

                if event == "connected":
                    onboard_wifi[mac] = now
                    print(f"[WIFI ENTRY] Device {mac} connected at {now.strftime('%H:%M:%S')}")

                elif event == "disconnected":
                    if mac in onboard_wifi:
                        entry_time = onboard_wifi.pop(mac)
                        duration = (now - entry_time).total_seconds() / 60
                        fare = calculate_fare(duration)
                        log_trip(mac, f"WiFiUser_{mac[-4:]}", entry_time, now, round(duration, 2), fare, "wifi")
                        print(f"[WIFI EXIT] Device {mac} disconnected at {now.strftime('%H:%M:%S')} | Duration: {duration:.1f} min | Fare: ₹{fare}")

            # ----------------- RFID EVENTS -----------------
            elif msg_type == "rfid":
                uid = obj.get("uid", "").upper()
                name = mapping.get(uid, f"RFID_{uid}")
                if uid not in onboard_rfid:
                    onboard_rfid[uid] = now
                    print(f"[RFID ENTRY] {name} tapped in at {now.strftime('%H:%M:%S')}")
                else:
                    entry_time = onboard_rfid.pop(uid)
                    duration = (now - entry_time).total_seconds() / 60
                    fare = calculate_fare(duration)
                    log_trip(uid, name, entry_time, now, round(duration, 2), fare, "rfid")
                    print(f"[RFID EXIT] {name} tapped out at {now.strftime('%H:%M:%S')} | Duration: {duration:.1f} min | Fare: ₹{fare}")

            # ----------------- Display and update current count -----------------
            total_passengers = len(onboard_rfid) + len(onboard_wifi)
            print(f"Current passengers: {total_passengers} (RFID: {len(onboard_rfid)}, WiFi: {len(onboard_wifi)})\n")
            update_passenger_count(len(onboard_rfid), len(onboard_wifi))

    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        ser.close()

if __name__ == "__main__":
    main()
