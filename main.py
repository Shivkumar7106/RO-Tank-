import os
import json

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import psycopg
import paho.mqtt.client as mqtt


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="RO Plant IoT Backend",
    version="1.0.0"
)


# ============================================================
# CORS CONFIGURATION
# ============================================================

allowed_origins_env = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173"
)
allowed_origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if "*" not in allowed_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MQTT CONFIGURATION
# ============================================================

BROKER = os.getenv("MQTT_BROKER")
PORT = int(os.getenv("MQTT_PORT", "8883"))
USERNAME = os.getenv("MQTT_USERNAME")
PASSWORD = os.getenv("MQTT_PASSWORD")
TOPIC = os.getenv("MQTT_TOPIC")


# ============================================================
# POSTGRESQL CONNECTION HELPER
# ============================================================

def get_db_connection():
    """
    Establishes a connection to PostgreSQL database using DATABASE_URL if available,
    or fallback to individual environment variables / defaults.
    """
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return psycopg.connect(db_url)
    
    return psycopg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "roplant"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "")
    )


# ============================================================
# BASIC API ROUTES
# ============================================================

@app.get("/")
def home():
    return {
        "message": "RO Plant IoT Backend is running!"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


@app.get("/cors-test")
def cors_test():
    return {
        "message": "CORS is working"
    }


# ============================================================
# SAVE MQTT DATA TO POSTGRESQL
# ============================================================

def save_to_database(data):
    try:
        conn = get_db_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tank_telemetry
                (
                    tank_id,
                    level_percent,
                    distance_cm,
                    volume_liters,
                    last_seen
                )
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (
                    data.get("tank_id"),
                    data.get("level_percent"),
                    data.get("distance_cm"),
                    data.get("volume_liters")
                )
            )

        conn.commit()
        conn.close()

        print("💾 Data saved to PostgreSQL!")

    except Exception as e:
        print("❌ Database error:", e)


# ============================================================
# LOW WATER ALERT DETECTION
# ============================================================

def check_low_water_alert(data):
    tank_id = data.get("tank_id")
    level = data.get("level_percent")
    volume = data.get("volume_liters")

    if tank_id is None or level is None:
        return

    try:
        conn = get_db_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM alerts
                WHERE tank_id = %s
                  AND alert_type = 'LOW_WATER'
                  AND status = 'active'
                LIMIT 1
                """,
                (tank_id,)
            )

            active_alert = cursor.fetchone()

            if level < 20 and active_alert is None:
                cursor.execute(
                    """
                    INSERT INTO alerts
                    (
                        tank_id,
                        alert_type,
                        level_percent,
                        volume_liters,
                        status
                    )
                    VALUES (%s, %s, %s, %s, 'active')
                    """,
                    (
                        tank_id,
                        "LOW_WATER",
                        level,
                        volume
                    )
                )

                print("🚨 LOW WATER ALERT CREATED!")

            elif level >= 20 and active_alert is not None:
                cursor.execute(
                    """
                    UPDATE alerts
                    SET
                        status = 'resolved',
                        resolved_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (active_alert[0],)
                )

                print("✅ LOW WATER ALERT RESOLVED!")

        conn.commit()
        conn.close()

    except Exception as e:
        print("❌ Alert processing error:", e)


# ============================================================
# MQTT CALLBACK - CONNECT
# ============================================================

def on_connect(client, userdata, flags, reason_code, properties):
    print("Connected to HiveMQ!")
    print("Reason:", reason_code)

    if TOPIC:
        client.subscribe(TOPIC)
        print("Subscribed to:", TOPIC)


# ============================================================
# MQTT CALLBACK - MESSAGE
# ============================================================

def on_message(client, userdata, msg):
    try:
        data = json.loads(
            msg.payload.decode()
        )

        print("\n📡 MQTT DATA RECEIVED")
        print("Topic:", msg.topic)
        print("Data:", data)

        save_to_database(data)
        check_low_water_alert(data)

    except Exception as e:
        print("❌ MQTT processing error:", e)


# ============================================================
# CREATE MQTT CLIENT
# ============================================================

mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2
)

if USERNAME and PASSWORD:
    mqtt_client.username_pw_set(
        USERNAME,
        PASSWORD
    )

mqtt_client.tls_set()

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message


# ============================================================
# CONNECT TO HIVEMQ
# ============================================================

if BROKER:
    try:
        mqtt_client.connect(
            BROKER,
            PORT,
            60
        )

        print("🔌 Connecting to HiveMQ...")

        mqtt_client.loop_start()

    except Exception as e:
        print("❌ MQTT connection error:", e)
else:
    print("⚠️ MQTT_BROKER not set in environment. Skipping MQTT initialization.")


# ============================================================
# GET LATEST TANK DATA
# ============================================================

@app.get("/api/tanks/{tank_id}/latest")
def get_latest_tank_data(tank_id: str):
    try:
        conn = get_db_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    tank_id,
                    level_percent,
                    distance_cm,
                    volume_liters,
                    recorded_at
                FROM tank_telemetry
                WHERE tank_id = %s
                ORDER BY recorded_at DESC
                LIMIT 1
                """,
                (tank_id,)
            )

            row = cursor.fetchone()

        conn.close()

        if row is None:
            return {
                "message": "No data found"
            }

        return {
            "tank_id": row[0],
            "level_percent": row[1],
            "distance_cm": row[2],
            "volume_liters": row[3],
            "recorded_at": row[4]
        }

    except Exception as e:
        return {
            "error": str(e)
        }


# ============================================================
# GET TANK HISTORY
# ============================================================

@app.get("/api/tanks/{tank_id}/history")
def get_tank_history(tank_id: str, range: str = "1h"):
    ranges = {
        "15m": "15 minutes",
        "1h": "1 hour",
        "6h": "6 hours",
        "24h": "24 hours",
        "7d": "7 days"
    }

    interval = ranges.get(range)

    if interval is None:
        return {
            "error": "Invalid range. Use 15m, 1h, 6h, 24h or 7d."
        }

    try:
        conn = get_db_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    level_percent,
                    volume_liters,
                    distance_cm,
                    recorded_at
                FROM tank_telemetry
                WHERE tank_id = %s
                  AND recorded_at >= NOW() - INTERVAL '{interval}'
                ORDER BY recorded_at ASC
                """,
                (tank_id,)
            )

            rows = cursor.fetchall()

        conn.close()

        return [
            {
                "level": row[0],
                "volume": row[1],
                "distance": row[2],
                "time": row[3]
            }
            for row in rows
        ]

    except Exception as e:
        return {
            "error": str(e)
        }


# ============================================================
# GET TANK ALERT HISTORY
# ============================================================

@app.get("/api/tanks/{tank_id}/alerts")
def get_tank_alerts(tank_id: str):
    try:
        conn = get_db_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    tank_id,
                    alert_type,
                    level_percent,
                    volume_liters,
                    status,
                    created_at,
                    resolved_at
                FROM alerts
                WHERE tank_id = %s
                ORDER BY created_at DESC
                """,
                (tank_id,)
            )

            rows = cursor.fetchall()

        conn.close()

        return [
            {
                "id": row[0],
                "tank_id": row[1],
                "alert_type": row[2],
                "level_percent": row[3],
                "volume_liters": row[4],
                "status": row[5],
                "created_at": row[6],
                "resolved_at": row[7]
            }
            for row in rows
        ]

    except Exception as e:
        return {
            "error": str(e)
        }


# ============================================================
# GET TANK SENSOR STATUS
# ============================================================

@app.get("/api/tanks/{tank_id}/status")
def get_tank_status(tank_id: str):
    try:
        conn = get_db_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    last_seen
                FROM tank_telemetry
                WHERE tank_id = %s
                ORDER BY recorded_at DESC
                LIMIT 1
                """,
                (tank_id,)
            )

            row = cursor.fetchone()

            if row is None:
                conn.close()
                return {
                    "tank_id": tank_id,
                    "status": "offline",
                    "last_seen": None
                }

            last_seen = row[0]

            cursor.execute(
                """
                SELECT
                    CASE
                        WHEN %s >= NOW() - INTERVAL '15 seconds'
                        THEN 'online'
                        ELSE 'offline'
                    END
                """,
                (last_seen,)
            )

            status = cursor.fetchone()[0]

        conn.close()

        return {
            "tank_id": tank_id,
            "status": status,
            "last_seen": last_seen
        }

    except Exception as e:
        return {
            "error": str(e)
        }
