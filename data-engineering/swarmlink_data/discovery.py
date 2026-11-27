"""Authenticated IPv4 LAN discovery for paired robots (standard library only)."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import subprocess
import threading
import time

SERVICE = "swarmlink-discovery-v1"
DEFAULT_PORT = 8766
MAX_PACKET = 1024


def subnet_targets(interfaces: list[dict]) -> list[str]:
    """Limit discovery to directly attached private IPv4 networks, at most /24."""
    targets = []
    interfaces = sorted(interfaces, key=lambda item: not item.get("ifname", "").startswith("wl"))
    for interface in interfaces:
        if interface.get("ifname", "").startswith(("lo", "docker", "br-", "veth", "virbr")):
            continue
        if "BROADCAST" not in interface.get("flags", []):
            continue
        for address in interface.get("addr_info", []):
            if address.get("family") != "inet":
                continue
            try:
                local = ipaddress.IPv4Address(address["local"])
                if not local.is_private or local.is_loopback or local.is_link_local:
                    continue
                network = ipaddress.IPv4Network(f"{local}/{max(24, int(address['prefixlen']))}", strict=False)
            except (ValueError, KeyError):
                continue
            targets.extend(str(host) for host in network.hosts() if host != local)
    return list(dict.fromkeys(targets))[:254]


def local_targets() -> list[str]:
    try:
        result = subprocess.run(["ip", "-j", "-4", "addr", "show", "up"], capture_output=True, text=True, timeout=2, check=True)
        return ["255.255.255.255", *subnet_targets(json.loads(result.stdout))]
    except (OSError, ValueError, subprocess.SubprocessError):
        return ["255.255.255.255"]


def signed(document: dict, token: str) -> dict:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return {**document, "signature": hmac.new(token.encode(), encoded, hashlib.sha256).hexdigest()}


def verified(document, token: str) -> bool:
    if not isinstance(document, dict) or not isinstance(document.get("signature"), str):
        return False
    unsigned = {k: v for k, v in document.items() if k != "signature"}
    expected = signed(unsigned, token)["signature"]
    return hmac.compare_digest(expected.encode(), document["signature"].encode())


def offer_url(packet: dict, token: str, robot_id: str, nonce: str, source_ip: str) -> str | None:
    """Bind the proof to this request, robot, destination IP, and HTTP port."""
    if not verified(packet, token):
        return None
    if (packet.get("service"), packet.get("type"), packet.get("robot_id"), packet.get("nonce")) != (SERVICE, "offer", robot_id, nonce):
        return None
    if packet.get("host") != source_ip or type(packet.get("port")) is not int or not 1 <= packet["port"] <= 65535:
        return None
    try:
        if not ipaddress.IPv4Address(source_ip).is_private:
            return None
    except ipaddress.AddressValueError:
        return None
    return f"http://{source_ip}:{packet['port']}"


def discover(robot_id: str, token: str, port: int = DEFAULT_PORT,
             target: str = "auto", timeout: float = 2) -> str | None:
    nonce = secrets.token_hex(16)
    probe = signed({"service": SERVICE, "type": "probe", "robot_id": robot_id, "nonce": nonce}, token)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        channel.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        channel.bind(("0.0.0.0", 0))
        destinations = local_targets() if target == "auto" else [target]
        encoded = json.dumps(probe).encode()
        for destination in destinations:
            try:
                channel.sendto(encoded, (destination, port))
            except OSError:
                continue
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            channel.settimeout(max(.01, deadline - time.monotonic()))
            try:
                data, address = channel.recvfrom(MAX_PACKET)
                endpoint = offer_url(json.loads(data), token, robot_id, nonce, address[0])
                if endpoint:
                    return endpoint
            except (ValueError, TypeError):
                continue
            except socket.timeout:
                break
    return None


class DiscoveryResponder(threading.Thread):
    def __init__(self, keys: dict[str, str], http_port: int, host: str = "0.0.0.0", port: int = DEFAULT_PORT):
        super().__init__(name="swarmlink-discovery", daemon=True)
        self.keys, self.http_port = keys, http_port
        self.stopping = threading.Event()
        self.channel = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.channel.bind((host, port))
            self.channel.settimeout(.25)
        except BaseException:
            self.channel.close()
            raise
        self.port = self.channel.getsockname()[1]

    def run(self):
        while not self.stopping.is_set():
            try:
                data, address = self.channel.recvfrom(MAX_PACKET)
                probe = json.loads(data)
                if not isinstance(probe, dict):
                    continue
                token = self.keys.get(probe.get("robot_id"))
                nonce = probe.get("nonce")
                if not token or not verified(probe, token):
                    continue
                if probe.get("service") != SERVICE or probe.get("type") != "probe" or not isinstance(nonce, str) or len(nonce) != 32:
                    continue
                # Determine the actual local interface used to reach this robot.
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                    route.connect(address)
                    host = route.getsockname()[0]
                offer = signed({"service": SERVICE, "type": "offer", "robot_id": probe["robot_id"],
                                "nonce": nonce, "host": host, "port": self.http_port}, token)
                self.channel.sendto(json.dumps(offer).encode(), address)
            except socket.timeout:
                continue
            except (ValueError, TypeError, OSError):
                if self.stopping.is_set():
                    break

    def stop(self):
        self.stopping.set()
        self.channel.close()
        if self.is_alive():
            self.join(timeout=2)
