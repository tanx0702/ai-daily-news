import unittest
import json
from pathlib import Path


class DeploymentConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def test_nginx_template_uses_domain_and_blocks_debug_artifacts(self):
        template = (self.root / "nginx" / "nginx.conf.template").read_text(encoding="utf-8")

        self.assertIn("server_name ${DOMAIN};", template)
        self.assertIn("/etc/letsencrypt/live/${DOMAIN}/fullchain.pem", template)
        self.assertIn("location ^~ /debug/", template)
        self.assertIn("return 404;", template)
        self.assertIn("location ^~ /editorial-review", template)
        self.assertIn("proxy_pass http://web:5000;", template)

    def test_compose_mounts_nginx_template_and_defines_web_healthcheck(self):
        compose = (self.root / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("./nginx/nginx.conf.template:/etc/nginx/templates/default.conf.template:ro", compose)
        self.assertIn("./templates:/app/templates:ro", compose)
        self.assertIn("./runtime:/app/runtime", compose)
        self.assertIn("healthcheck:", compose)
        self.assertIn("condition: service_healthy", compose)

    def test_compose_publishes_nginx_on_public_network_and_keeps_app_internal(self):
        compose = (self.root / "docker-compose.yml").read_text(encoding="utf-8")

        nginx_start = compose.index("  nginx:\n")
        nginx_end = compose.index("\nnetworks:\n", nginx_start)
        nginx = compose[nginx_start:nginx_end]

        self.assertIn("networks:\n      - app\n      - public", nginx)
        self.assertIn("  public:\n    driver: bridge", compose)
        self.assertIn("  app:\n    internal: true", compose)

    def test_compose_default_no_proxy_bypasses_wechat_api(self):
        compose = (self.root / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn(
            "AI_NEWS_NO_PROXY:-localhost,127.0.0.1,web,nginx,proxy,api.weixin.qq.com",
            compose,
        )

    def test_compose_keeps_the_egress_proxy_internal_and_routes_web_through_it(self):
        compose = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        proxy_start = compose.index("  proxy:\n")
        proxy_end = compose.index("\n  web:\n", proxy_start)
        proxy = compose[proxy_start:proxy_end]

        self.assertIn("image: ai-news-web:latest", proxy)
        self.assertIn("pull_policy: never", proxy)
        self.assertIn('profiles: ["egress-proxy"]', proxy)
        self.assertIn(
            "${AI_NEWS_PROXY_BINARY_PATH:-./config/sing-box-unavailable}:/proxy-bin/sing-box:ro",
            proxy,
        )
        self.assertIn(
            "${AI_NEWS_PROXY_CONFIG_PATH:-./config/sing-box-blocked.json}:/etc/sing-box/config.json:ro",
            proxy,
        )
        self.assertNotIn("ports:", proxy)
        self.assertIn("networks:\n      - egress", proxy)
        self.assertIn("networks:\n      - app\n      - egress", compose)
        self.assertIn("networks:\n      - app", compose)
        self.assertIn("  app:\n    internal: true", compose)
        self.assertIn("HTTP_PROXY: ${AI_NEWS_HTTP_PROXY:-}", compose)
        self.assertIn("HTTPS_PROXY: ${AI_NEWS_HTTPS_PROXY:-}", compose)
        self.assertIn(
            "NO_PROXY: ${AI_NEWS_NO_PROXY:-localhost,127.0.0.1,web,nginx,proxy,api.weixin.qq.com}",
            compose,
        )

    def test_default_sing_box_configuration_blocks_egress_until_a_private_config_is_mounted(self):
        config = json.loads((self.root / "config" / "sing-box-blocked.json").read_text(encoding="utf-8"))

        self.assertEqual(config["inbounds"][0]["type"], "mixed")
        self.assertEqual(config["inbounds"][0]["listen_port"], 7890)
        self.assertEqual(config["outbounds"], [{"type": "block", "tag": "blocked"}])
        self.assertEqual(config["route"]["final"], "blocked")

    def test_dockerignore_excludes_environment_backup_files(self):
        patterns = (self.root / ".dockerignore").read_text(encoding="utf-8").splitlines()

        self.assertIn(".env.bak*", patterns)

    def test_advanced_environment_template_does_not_expose_obsolete_editorial_mode(self):
        template = (self.root / ".env.advanced.example").read_text(encoding="utf-8")

        self.assertNotIn("DAILY_EDITORIAL_MODE", template)


if __name__ == "__main__":
    unittest.main()
