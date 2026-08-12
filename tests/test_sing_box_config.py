import unittest

from scripts.generate_sing_box_config import ProxyConfigError, build_proxy_config


class SingBoxConfigTests(unittest.TestCase):
    def test_builds_a_tls_websocket_vless_proxy_without_a_direct_fallback(self):
        config = build_proxy_config(
            "vless://11111111-1111-4111-8111-111111111111@example.com:443"
            "?security=tls&type=ws&path=%2Fws&host=cdn.example.com&sni=origin.example.com&fp=chrome"
        )

        outbound = config["outbounds"][0]
        self.assertEqual(outbound["type"], "vless")
        self.assertEqual(outbound["server"], "example.com")
        self.assertEqual(outbound["server_port"], 443)
        self.assertEqual(outbound["tls"]["server_name"], "origin.example.com")
        self.assertEqual(outbound["transport"]["type"], "ws")
        self.assertEqual(outbound["transport"]["path"], "/ws")
        self.assertEqual(outbound["transport"]["headers"]["Host"], "cdn.example.com")
        self.assertEqual(config["route"]["final"], "proxy")
        self.assertNotIn("direct", [entry["tag"] for entry in config["outbounds"]])

    def test_builds_a_tcp_reality_vless_proxy_with_required_parameters(self):
        config = build_proxy_config(
            "vless://11111111-1111-4111-8111-111111111111@example.com:443"
            "?security=reality&type=tcp&sni=origin.example.com&pbk=public-key&sid=0123456789abcdef"
            "&flow=xtls-rprx-vision&fp=chrome&encryption=none&headerType=none"
        )

        outbound = config["outbounds"][0]
        self.assertEqual(outbound["flow"], "xtls-rprx-vision")
        self.assertNotIn("transport", outbound)
        self.assertEqual(outbound["tls"]["server_name"], "origin.example.com")
        self.assertEqual(outbound["tls"]["reality"], {
            "enabled": True,
            "public_key": "public-key",
            "short_id": "0123456789abcdef",
        })

    def test_rejects_a_vless_node_without_an_accepted_transport_or_reality_parameters(self):
        with self.assertRaisesRegex(ProxyConfigError, r"WebSocket \+ TLS 或 TCP \+ Reality"):
            build_proxy_config(
                "vless://11111111-1111-4111-8111-111111111111@example.com:443"
                "?security=tls&type=grpc"
            )

        with self.assertRaisesRegex(ProxyConfigError, "Reality"):
            build_proxy_config(
                "vless://11111111-1111-4111-8111-111111111111@example.com:443"
                "?security=reality&type=tcp&sni=origin.example.com&sid=0123456789abcdef"
            )

    def test_rejects_an_invalid_port_or_tls_fingerprint(self):
        invalid_port = (
            "vless://11111111-1111-4111-8111-111111111111@example.com:0"
            "?security=tls&type=ws"
        )
        invalid_fingerprint = (
            "vless://11111111-1111-4111-8111-111111111111@example.com:443"
            "?security=tls&type=ws&fp=unknown"
        )

        with self.assertRaisesRegex(ProxyConfigError, "端口"):
            build_proxy_config(invalid_port)
        with self.assertRaisesRegex(ProxyConfigError, "fingerprint"):
            build_proxy_config(invalid_fingerprint)


if __name__ == "__main__":
    unittest.main()
