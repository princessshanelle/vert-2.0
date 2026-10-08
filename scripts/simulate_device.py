#!/usr/bin/env python3
"""Pretend to be a provisioned VERT sensor, without owning any hardware yet.

Nobody should have to solder a DHT22 to a breadboard just to find out
whether their docker-compose stack is wired up correctly. This script
speaks exactly the MQTT contract firmware/dht22-node/src/main.cpp does --
same topics, same envelope, same announce-once-then-read-forever shape --
so it's a faithful stand-in while you get the backend running, and a
useful thing to point at a rule you're testing before you trust it near
an actual plant.

Usage:
    pip install paho-mqtt
    python scripts/simulate_device.py --device-id dht22-01 --secret <the-provisioned-secret> \
        --zone zoneA --broker localhost --temp 42.0

Provision the device first (see docs/TESTING.md):
    curl -s -X POST http://localhost:8080/api/v1/devices/provision \\
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \\
      -d '{"device_id":"dht22-01","device_type":"temp-humidity"}'
"""
import argparse
import json
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--device-id", required=True)
    p.add_argument("--secret", required=True, help="the secret /api/v1/devices/provision returned")
    p.add_argument("--zone", default="zoneA")
    p.add_argument("--device-type", default="temp-humidity")
    p.add_argument("--broker", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--temp", type=float, default=23.4, help="temperature to report, in Celsius")
    p.add_argument("--humidity", type=float, default=55.0)
    p.add_argument("--interval", type=float, default=5.0, help="seconds between readings")
    p.add_argument("--count", type=int, default=0, help="number of readings to send, 0 = forever")
    args = p.parse_args()

    topic_reading = f"vert/{args.zone}/{args.device_type}/{args.device_id}/reading"
    topic_announce = f"vert/{args.zone}/{args.device_type}/{args.device_id}/announce"

    client = mqtt.Client(client_id=args.device_id)
    client.username_pw_set(args.device_id, args.secret)
    client.connect(args.broker, args.port, keepalive=60)
    client.loop_start()

    client.publish(topic_announce, json.dumps({
        "kind": "announce",
        "device_id": args.device_id,
        "device_type": args.device_type,
        "capabilities": {"reads": ["temp_c", "humidity_pct"]},
    }), retain=True)
    print(f"announced {args.device_id} on {topic_announce}")

    seq = 0
    sent = 0
    try:
        while args.count == 0 or sent < args.count:
            envelope = {
                "schema_version": "1.0",
                "device_id": args.device_id,
                "device_type": args.device_type,
                "zone": args.zone,
                "ts": datetime.now(timezone.utc).isoformat(),
                "seq": seq,
                "payload": {"temp_c": args.temp, "humidity_pct": args.humidity},
            }
            client.publish(topic_reading, json.dumps(envelope))
            print(f"published reading #{seq}: temp_c={args.temp} humidity_pct={args.humidity}")
            seq += 1
            sent += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
