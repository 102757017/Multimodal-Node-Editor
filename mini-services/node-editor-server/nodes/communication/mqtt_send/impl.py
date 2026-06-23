"""MQTT Send node — publishes the input payload to an MQTT topic.

The MQTT client lives on the ComputeLogic instance and persists across
compute() calls.  Property changes (broker, credentials, …) trigger a
reconnect.  The ``topic`` input port, when connected, overrides the
``topic`` property — this lets upstream nodes dynamically select the
destination topic per frame.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, Optional

from node_editor.node_def import ComputeLogic

try:
    import paho.mqtt.client as mqtt
    _PAHO_AVAILABLE = True
except ImportError:  # pragma: no cover
    mqtt = None
    _PAHO_AVAILABLE = False


class MqttSendLogic(ComputeLogic):
    """Publish payloads to an MQTT topic."""

    def __init__(self):
        self._lock = threading.Lock()
        self._client: Optional[Any] = None
        self._connected: bool = False
        self._conn_signature: Optional[tuple] = None

    # ------------------------------------------------------------------ #
    # paho-mqtt callbacks
    # ------------------------------------------------------------------ #
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            with self._lock:
                self._connected = True
        else:
            print(f"[mqtt_send] connect failed rc={rc}")

    def _on_disconnect(self, client, userdata, rc, properties=None):
        with self._lock:
            self._connected = False
        if rc != 0:
            print(f"[mqtt_send] unexpected disconnect rc={rc}")

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #
    def _signature(self, properties: Dict[str, Any]) -> tuple:
        return (
            str(properties.get("broker_host", "localhost")),
            int(properties.get("broker_port", 1883)),
            str(properties.get("username", "")),
            str(properties.get("password", "")),
        )

    def _ensure_client(self, properties: Dict[str, Any]):
        sig = self._signature(properties)
        if self._client is not None and self._conn_signature == sig:
            return
        self._teardown_client()
        if not _PAHO_AVAILABLE:
            return
        client_id_prefix = str(properties.get("client_id_prefix", "mne_send_"))
        client_id = f"{client_id_prefix}{uuid.uuid4().hex[:8]}"
        try:
            client = mqtt.Client(
                client_id=client_id,
                protocol=mqtt.MQTTv311,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            )
        except Exception:
            client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        username = str(properties.get("username", "") or "")
        password = str(properties.get("password", "") or "")
        if username:
            client.username_pw_set(username, password)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        try:
            client.connect_async(
                str(properties.get("broker_host", "localhost")),
                int(properties.get("broker_port", 1883)),
                keepalive=60,
            )
            client.loop_start()
        except Exception as e:
            print(f"[mqtt_send] connect error: {e}")
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
        self._teardown_client()

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
                "sent": False,
                "error": "paho-mqtt is not installed (pip install paho-mqtt)",
                "__error__": "paho-mqtt is not installed (pip install paho-mqtt)",
            }

        self._ensure_client(properties)

        # resolve topic: input port overrides property
        topic_input = inputs.get("topic")
        topic = str(topic_input) if topic_input else str(properties.get("topic", "test/out"))
        if not topic:
            return {"sent": False, "error": "no topic configured"}

        payload = inputs.get("payload")
        if payload is None:
            return {"sent": False, "error": ""}

        # serialise payload — strings/bytes go as-is, everything else as str()
        if isinstance(payload, bytes):
            payload_bytes = payload
        elif isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = str(payload).encode("utf-8")

        qos = int(properties.get("qos", 0))
        retain = bool(properties.get("retain", False))

        with self._lock:
            client = self._client
            connected = self._connected

        if client is None:
            return {"sent": False, "error": "MQTT client not initialised"}
        if not connected:
            # allow a brief grace period for the async connect to complete
            time.sleep(0.05)
            with self._lock:
                connected = self._connected
            if not connected:
                return {"sent": False, "error": "not connected to broker"}

        info = client.publish(topic, payload=payload_bytes, qos=qos, retain=retain)
        # for QoS 0, info.rc is the only useful field; for QoS 1/2 we could
        # wait for publish confirmation but that would block compute().
        ok = (info.rc == mqtt.MQTT_ERR_SUCCESS) if hasattr(mqtt, "MQTT_ERR_SUCCESS") else (info.rc == 0)
        return {
            "sent": bool(ok),
            "error": "" if ok else f"publish rc={info.rc}",
        }
