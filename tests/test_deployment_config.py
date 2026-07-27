import unittest
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
        self.assertIn("healthcheck:", compose)
        self.assertIn("condition: service_healthy", compose)

    def test_dockerignore_excludes_environment_backup_files(self):
        patterns = (self.root / ".dockerignore").read_text(encoding="utf-8").splitlines()

        self.assertIn(".env.bak*", patterns)


if __name__ == "__main__":
    unittest.main()
