import os
import subprocess
import sys
import tempfile

from django.test import SimpleTestCase


class DeploymentSettingsTests(SimpleTestCase):
    def run_settings(self, script="import config.settings", **values):
        environment = {**os.environ}
        for name in (
            "DEPLOYMENT_MODE",
            "DJANGO_SECRET_KEY",
            "EMAIL_BACKEND",
            "DEFAULT_FROM_EMAIL",
            "DEBUG",
            "TRUST_PROXY_HEADERS",
            "SECURE_HSTS_SECONDS",
            "POLAR_BILLING_ENABLED",
            "POLAR_ACCESS_TOKEN",
            "POLAR_WEBHOOK_SECRET",
            "POLAR_MONTHLY_PRODUCT_ID",
            "POLAR_ANNUAL_PRODUCT_ID",
            "POLAR_BETA_DISCOUNT_ID",
            "POLAR_SERVER_URL",
            "POLAR_ALLOW_CUSTOM_SERVER_URL",
            "CLOUD_BETA_ACCESS",
            "PUBLIC_BASE_URL",
        ):
            environment.pop(name, None)
        environment.update(values)
        return subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )

    def test_invalid_deployment_mode_fails_startup(self):
        result = self.run_settings(DEPLOYMENT_MODE="invalid")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DEPLOYMENT_MODE must be 'self_hosted' or 'cloud'", result.stderr)

    def test_owner_setup_deadline_persists_across_restarts(self):
        with tempfile.TemporaryDirectory() as data_dir:
            script = "from config import settings; print(settings.OWNER_SETUP_DEADLINE, settings.OWNER_SETUP_TOKEN)"
            first = self.run_settings(script, DATA_DIR=data_dir)
            second = self.run_settings(script, DATA_DIR=data_dir)
            token_mode = os.stat(os.path.join(data_dir, "owner_setup_token")).st_mode & 0o777

        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(token_mode, 0o600)

    def test_cloud_requires_explicit_secret_and_email_delivery(self):
        result = self.run_settings(DEPLOYMENT_MODE="cloud")
        self.assertIn("explicit non-empty DJANGO_SECRET_KEY", result.stderr)

        for backend in (None, "", "django.core.mail.backends.console.EmailBackend"):
            values = {
                "DEPLOYMENT_MODE": "cloud",
                "DJANGO_SECRET_KEY": "test-cloud-secret",
                "DEFAULT_FROM_EMAIL": "noreply@example.com",
                "POLAR_BILLING_ENABLED": "false",
            }
            if backend is not None:
                values["EMAIL_BACKEND"] = backend
            with self.subTest(backend=backend):
                result = self.run_settings(**values)
                self.assertIn("explicit non-console EMAIL_BACKEND", result.stderr)

        result = self.run_settings(
            DEPLOYMENT_MODE="cloud",
            DJANGO_SECRET_KEY="test-cloud-secret",
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            DEFAULT_FROM_EMAIL="noreply@localhost",
            POLAR_BILLING_ENABLED="false",
        )
        self.assertIn("explicit valid DEFAULT_FROM_EMAIL", result.stderr)

    def test_self_hosted_email_default_fails_closed(self):
        result = self.run_settings("from config import settings; print(settings.EMAIL_BACKEND)")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "django.core.mail.backends.smtp.EmailBackend")

        console = self.run_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
        self.assertNotEqual(console.returncode, 0)
        self.assertIn("not allowed outside DEBUG mode", console.stderr)

    def test_email_verification_is_only_mandatory_in_cloud(self):
        script = "from config import settings; print(settings.ACCOUNT_EMAIL_VERIFICATION)"
        self_hosted = self.run_settings(script)
        cloud = self.run_settings(
            script,
            DEPLOYMENT_MODE="cloud",
            DJANGO_SECRET_KEY="test-cloud-secret",
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            DEFAULT_FROM_EMAIL="noreply@example.com",
            POLAR_BILLING_ENABLED="false",
        )

        self.assertEqual(self_hosted.returncode, 0)
        self.assertEqual(self_hosted.stdout.strip(), "none")
        self.assertEqual(cloud.returncode, 0)
        self.assertEqual(cloud.stdout.strip(), "mandatory")

    def test_cloud_trusts_proxy_headers_only_with_explicit_opt_in(self):
        script = "from config import settings; print(getattr(settings, 'SECURE_PROXY_SSL_HEADER', None))"
        values = {
            "DEPLOYMENT_MODE": "cloud",
            "DJANGO_SECRET_KEY": "test-cloud-secret",
            "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "DEFAULT_FROM_EMAIL": "noreply@example.com",
            "POLAR_BILLING_ENABLED": "false",
        }

        default = self.run_settings(script, **values)
        trusted_proxy = self.run_settings(script, **values, TRUST_PROXY_HEADERS="true")

        self.assertEqual(default.returncode, 0)
        self.assertEqual(default.stdout.strip(), "None")
        self.assertEqual(trusted_proxy.returncode, 0)
        self.assertIn("('HTTP_X_FORWARDED_PROTO', 'https')", trusted_proxy.stdout)

    def test_cloud_enables_hsts_by_default(self):
        result = self.run_settings(
            "from config import settings; print(settings.SECURE_HSTS_SECONDS)",
            DEPLOYMENT_MODE="cloud",
            DJANGO_SECRET_KEY="test-cloud-secret",
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            DEFAULT_FROM_EMAIL="noreply@example.com",
            POLAR_BILLING_ENABLED="false",
        )
        self.assertEqual(result.stdout.strip(), "31536000")

    def test_cloud_billing_requires_complete_server_configuration(self):
        result = self.run_settings(
            DEPLOYMENT_MODE="cloud",
            DJANGO_SECRET_KEY="test-cloud-secret",
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            DEFAULT_FROM_EMAIL="noreply@example.com",
        )
        self.assertIn("Cloud billing requires", result.stderr)
        self.assertNotIn("polar-secret", result.stderr)

    def test_polar_server_url_is_limited_to_official_https_origins(self):
        values = {
            "DEPLOYMENT_MODE": "cloud",
            "DJANGO_SECRET_KEY": "test-cloud-secret",
            "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "DEFAULT_FROM_EMAIL": "noreply@example.com",
            "POLAR_ACCESS_TOKEN": "token",
            "POLAR_WEBHOOK_SECRET": "secret",
            "POLAR_MONTHLY_PRODUCT_ID": "monthly",
            "POLAR_ANNUAL_PRODUCT_ID": "annual",
            "POLAR_BETA_DISCOUNT_ID": "discount",
            "PUBLIC_BASE_URL": "https://app.example.com",
        }
        insecure = self.run_settings(**values, POLAR_SERVER_URL="http://api.polar.sh")
        custom = self.run_settings(**values, POLAR_SERVER_URL="https://polar.invalid")
        production_override = self.run_settings(
            **values, POLAR_SERVER_URL="https://polar.invalid", POLAR_ALLOW_CUSTOM_SERVER_URL="true"
        )
        debug_override = self.run_settings(
            **values,
            DEBUG="true",
            POLAR_SERVER_URL="https://polar.invalid",
            POLAR_ALLOW_CUSTOM_SERVER_URL="true",
        )

        self.assertIn("must use HTTPS", insecure.stderr)
        self.assertIn("official Polar HTTPS API origin", custom.stderr)
        self.assertNotEqual(production_override.returncode, 0)
        self.assertEqual(debug_override.returncode, 0)

    def test_cloud_beta_access_defaults_off(self):
        result = self.run_settings(
            "from config import settings; print(settings.CLOUD_BETA_ACCESS)",
            DEPLOYMENT_MODE="cloud",
            DJANGO_SECRET_KEY="test-cloud-secret",
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            DEFAULT_FROM_EMAIL="noreply@example.com",
            POLAR_BILLING_ENABLED="false",
        )
        self.assertEqual(result.stdout.strip(), "False")
