"""MQTT Receive node — subscribes to a topic (with wildcard support) and
caches the last received payload.

Behaviour (per the user spec):
  * A background paho-mqtt client maintains the subscription and updates a
    thread-safe cache whenever a new message arrives.
  * compute() always returns the cached payload (every frame).  This lets
    downstream nodes that need data every frame pick it up from this node's
    output port.
  * The ``new`` output port is ``True`` for exactly one frame after a new
    message arrives, then resets to ``False`` until the next message.
  * The cache is never cleared; if no message ever arrives, ``payload`` is
    an empty string and ``new`` is ``False``.
  * Topic wildcards ``+`` (single level) and ``#`` (multi level) are handled
    natively by paho-mqtt's subscription mechanism.

The MQTT client lives on the ComputeLogic instance and persists across
compute() calls.  On the first compute() call it connects and subscribes;
subsequent calls just read the cache.  Property changes (broker, topic, etc.)
trigger a reconnect.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Dict, Optional

from node_editor.node_def import ComputeLogic

try:
    import paho.mqtt.client as mqtt
    _PAHO_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional dep
    mqtt = None
    _PAHO_AVAILABLE = False


class MqttReceiveLogic(ComputeLogic):
    """Subscribe to an MQTT topic and output the last received payload."""

    def __init__(self):
        self._lock = threading.Lock()
        # cached message
        self._cached_payload: str = ""
        self._cached_topic: str = ""
        self._cached_timestamp: float = 0.0
        # "new" flag — set True when a message arrives, cleared on compute()
        self._has_new: bool = False
        # client state
        self._client: Optional[Any] = None
        self._connected: bool = False
        # signature of the current connection (used to detect property changes)
        self._conn_signature: Optional[tuple] = None

    # ------------------------------------------------------------------ #
    # paho-mqtt callbacks
    # ------------------------------------------------------------------ #
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            with self._lock:
                self._connected = True
            # subscribe on connect (paho requires this)
            topic = userdata.get("topic", "test/#")
            qos = int(userdata.get("qos", 0))
            try:
                client.subscribe(topic, qos=qos)
            except Exception as e:
                print(f"[mqtt_receive] subscribe failed: {e}")
        else:
            print(f"[mqtt_receive] connect failed rc={rc}")

    def _on_disconnect(self, client, userdata, rc, properties=None):
        with self._lock:
            self._connected = False
        if rc != 0:
            print(f"[mqtt_receive] unexpected disconnect rc={rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = str(msg.payload)
        with self._lock:
            self._cached_payload = payload
            self._cached_topic = msg.topic
            self._cached_timestamp = time.time()
            self._has_new = True

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #
    def _signature(self, properties: Dict[str, Any]) -> tuple:
        return (
            str(properties.get("broker_host", "localhost")),
            int(properties.get("broker_port", 1883)),
            str(properties.get("topic", "test/#")),
            str(properties.get("username", "")),
            str(properties.get("password", "")),
            int(properties.get("qos", 0)),
        )

    def _ensure_client(self, properties: Dict[str, Any]):
        """Create / recreate the MQTT client if the connection signature
        has changed (or if no client exists yet)."""
        sig = self._signature(properties)
        if self._client is not None and self._conn_signature == sig:
            return  # nothing to do
        # signature changed (or first call) — tear down and rebuild
        self._teardown_client()
        if not _PAHO_AVAILABLE:
            return
        client_id_prefix = str(properties.get("client_id_prefix", "mne_recv_"))
        client_id = f"{client_id_prefix}{uuid.uuid4().hex[:8]}"
        try:
            client = mqtt.Client(
                client_id=client_id,
                protocol=mqtt.MQTTv311,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            )
        except Exception:
            # older paho versions without CallbackAPIVersion
            client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        username = str(properties.get("username", "") or "")
        password = str(properties.get("password", "") or "")
        if username:
            client.username_pw_set(username, password)
        userdata = {
            "topic": str(properties.get("topic", "test/#")),
            "qos": int(properties.get("qos", 0)),
        }
        client.user_data_set(userdata)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        # network I/O on a background thread so compute() never blocks
        try:
            client.connect_async(
                str(properties.get("broker_host", "localhost")),
                int(properties.get("broker_port", 1883)),
                keepalive=60,
            )
            client.loop_start()
        except Exception as e:
            print(f"[mqtt_receive] connect error: {e}")
            return
        self._client = client
        self._conn_signature = sig

    def _teardown_client(self):
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self._connected = False
        self._conn_signature = None

    def reset(self):
        """Called when the engine is reset — close the MQTT client."""
        self._teardown_client()
        with self._lock:
            self._cached_payload = ""
            self._cached_topic = ""
            self._cached_timestamp = 0.0
            self._has_new = False

    # ------------------------------------------------------------------ #
    # Compute
    # ------------------------------------------------------------------ #
    def compute(
        self,
        inputs: Dict[str, Any],
        properties: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not _PAHO_AVAILABLE:
            return {
                "payload": "",
                "topic": "",
                "new": False,
                "timestamp": 0.0,
                "__error__": "paho-mqtt is not installed (pip install paho-mqtt)",
            }

        # lazily start / restart the MQTT client
        self._ensure_client(properties)

        with self._lock:
            payload = self._cached_payload
            topic = self._cached_topic
            ts = self._cached_timestamp
            is_new = self._has_new
            self._has_new = False  # consume the "new" flag

        return {
            "payload": payload,
            "topic": topic,
            "new": bool(is_new),
            "timestamp": float(ts),
            "__frame_count__": 1,
        }
