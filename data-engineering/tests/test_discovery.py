from swarmlink_data.discovery import DiscoveryResponder, SERVICE, discover, offer_url, signed, subnet_targets

TOKEN = "paired-robot-token-with-at-least-32-characters"


def test_discovery_uses_real_udp_and_authenticates_pairing():
    responder = DiscoveryResponder({"robot-01": TOKEN}, 9876, host="127.0.0.1", port=0)
    responder.start()
    try:
        assert discover("robot-01", TOKEN, responder.port, "127.0.0.1", .5) == "http://127.0.0.1:9876"
        assert discover("robot-01", "wrong-key", responder.port, "127.0.0.1", .1) is None
        assert discover("unknown-robot", TOKEN, responder.port, "127.0.0.1", .1) is None
    finally:
        responder.stop()
    assert not responder.is_alive()


def test_offer_cannot_change_target_or_replay_an_old_request():
    nonce = "1" * 32
    body = {"service": SERVICE, "type": "offer", "robot_id": "robot-01", "nonce": nonce, "host": "172.20.10.5", "port": 8765}
    packet = signed(body, TOKEN)
    assert offer_url(packet, TOKEN, "robot-01", nonce, "172.20.10.5") == "http://172.20.10.5:8765"
    assert offer_url(packet, TOKEN, "robot-01", "2" * 32, "172.20.10.5") is None
    assert offer_url(packet, TOKEN, "robot-02", nonce, "172.20.10.5") is None
    assert offer_url(packet, TOKEN, "robot-01", nonce, "172.20.10.6") is None
    assert offer_url({**packet, "host": "172.20.10.6"}, TOKEN, "robot-01", nonce, "172.20.10.6") is None
    assert TOKEN not in str(packet)


def test_public_offers_and_invalid_ports_are_rejected():
    body = {"service": SERVICE, "type": "offer", "robot_id": "robot-01", "nonce": "1" * 32, "host": "8.8.8.8", "port": 8765}
    assert offer_url(signed(body, TOKEN), TOKEN, "robot-01", "1" * 32, "8.8.8.8") is None
    for port in (0, 65536, True, "8765"):
        local = {**body, "host": "172.20.10.5", "port": port}
        assert offer_url(signed(local, TOKEN), TOKEN, "robot-01", "1" * 32, "172.20.10.5") is None


def test_hotspot_discovery_uses_current_subnet_without_hardcoded_pc_ip():
    def interface(name, ip, prefix):
        return {"ifname": name, "flags": ["UP", "BROADCAST"], "addr_info": [{"family": "inet", "local": ip, "prefixlen": prefix}]}
    targets = subnet_targets([interface("wlan0", "172.20.10.2", 28), interface("eth0", "169.254.1.1", 16), interface("docker0", "172.17.0.1", 16)])
    assert "172.20.10.5" in targets and "172.20.10.4" in targets
    assert len(targets) == 13
    assert "172.20.10.2" not in targets and "172.20.10.15" not in targets
    assert len(subnet_targets([interface("wlan0", "10.0.1.3", 8)])) <= 254
