import json
import logging
import threading
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from gateway.hardening import _scope, check_rate_limit, client_address
from gateway.logging_filters import RedactRequestSecrets
from gateway.models import RateLimitBucket


class RateLimitTests(TestCase):
    def test_login_limit_is_database_shared_and_identifiers_are_hashed(self):
        with patch.dict("gateway.hardening.RATE_LIMITS", {"login": (1, 300)}, clear=False):
            self.client.post("/login/", {"username": "nobody", "password": "secret"}, REMOTE_ADDR="203.0.113.8")
            response = Client().post(
                "/login/",
                {"username": "someone-else", "password": "different"},
                REMOTE_ADDR="203.0.113.8",
                HTTP_X_FORWARDED_FOR="198.51.100.2",
            )

        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)
        bucket = RateLimitBucket.objects.get(scope="login")
        self.assertEqual(len(bucket.identifier_hash), 64)
        self.assertNotIn("203.0.113.8", bucket.identifier_hash)

    @override_settings(TRUST_PROXY_HEADERS=False)
    def test_forwarded_address_requires_explicit_proxy_trust(self):
        request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="198.51.100.2", REMOTE_ADDR="203.0.113.8")
        self.assertEqual(client_address(request), "203.0.113.8")
        with override_settings(TRUST_PROXY_HEADERS=True):
            self.assertEqual(client_address(request), "198.51.100.2")

    def test_mcp_and_oauth_token_limits_return_protocol_safe_errors(self):
        cases = (
            ("mcp", "/mcp/", {"jsonrpc": "2.0", "method": "initialize", "id": 1}, "-32002"),
            ("oauth_token", "/oauth/token/", {}, "temporarily_unavailable"),
        )
        for index, (scope, path, body, marker) in enumerate(cases):
            with self.subTest(scope=scope), patch.dict(
                "gateway.hardening.RATE_LIMITS", {scope: (0, 60)}, clear=False
            ):
                response = self.client.post(
                    path,
                    data=json.dumps(body) if body else {},
                    content_type="application/json" if body else "application/x-www-form-urlencoded",
                    REMOTE_ADDR=f"203.0.113.{index + 20}",
                )
                self.assertEqual(response.status_code, 429)
                self.assertIn(marker, response.content.decode())
                self.assertIn("Retry-After", response)

    def test_owner_setup_and_oauth_revoke_are_rate_limited_through_middleware(self):
        cases = (
            ("signup", "/setup/", "203.0.113.31"),
            ("oauth_token", "/oauth/revoke/", "203.0.113.32"),
        )
        for scope, path, address in cases:
            with self.subTest(path=path), patch.dict(
                "gateway.hardening.RATE_LIMITS", {scope: (0, 60)}, clear=False
            ):
                response = self.client.post(path, REMOTE_ADDR=address)
            self.assertEqual(response.status_code, 429)
            self.assertGreater(int(response["Retry-After"]), 0)

    def test_all_sensitive_endpoint_routes_are_classified(self):
        factory = RequestFactory()
        cases = (
            (factory.post("/login/"), "login"),
            (factory.post("/accounts/signup/"), "signup"),
            (factory.post("/setup/"), "signup"),
            (factory.get("/accounts/github/login/"), "oauth_start"),
            (factory.get("/accounts/github/login/callback/"), "oauth_callback"),
            (factory.get("/oauth/authorize/"), "oauth_start"),
            (factory.post("/oauth/register/"), "oauth_start"),
            (factory.post("/integrations/add/"), "oauth_start"),
            (factory.post("/integrations/1/reconnect/"), "oauth_start"),
            (factory.post("/oauth/token/"), "oauth_token"),
            (factory.post("/oauth/revoke/"), "oauth_token"),
            (factory.post("/mcp/"), "mcp"),
            (factory.get("/connect/token/"), "credential_setup"),
        )
        for request, expected in cases:
            with self.subTest(path=request.path):
                self.assertEqual(_scope(request), expected)

    def test_expired_buckets_are_pruned_lazily(self):
        expired = RateLimitBucket.objects.create(scope="login", identifier_hash="old", window=1, count=1)
        RateLimitBucket.objects.filter(pk=expired.pk).update(updated_at=timezone.now() - timedelta(hours=3))

        with patch("gateway.hardening._next_prune_at", 0):
            check_rate_limit("login", "current-client")

        self.assertFalse(RateLimitBucket.objects.filter(pk=expired.pk).exists())


class RedactionAndHealthTests(TestCase):
    def test_access_and_security_logs_exclude_request_secrets(self):
        token = "setup-token-must-not-appear"
        with self.assertLogs("juraguard.access", level="INFO") as access_logs, self.assertLogs(
            "juraguard.security", level="INFO"
        ) as security_logs:
            response = self.client.get(
                f"/connect/{token}/?password=query-secret",
                HTTP_AUTHORIZATION="Bearer header-secret",
                HTTP_COOKIE="sessionid=cookie-secret",
                REMOTE_ADDR="203.0.113.44",
            )

        self.assertEqual(response.status_code, 404)
        output = "\n".join(access_logs.output + security_logs.output)
        for secret in (token, "query-secret", "header-secret", "cookie-secret"):
            self.assertNotIn(secret, output)
        self.assertIn('"route":"credential_setup"', output)
        self.assertIn('"event":"setup_link_failed"', output)

    def test_django_request_and_csrf_logs_redact_setup_and_query_secrets(self):
        setup_secret = "setup-token-must-not-appear"
        query_secret = "query-code-must-not-appear"
        path = f"/connect/{setup_secret}/?code={query_secret}"
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactRequestSecrets())
        request = RequestFactory().get(path, HTTP_AUTHORIZATION="Bearer header-secret")
        loggers = [logging.getLogger("django.request"), logging.getLogger("django.security.csrf")]
        for logger in loggers:
            logger.addHandler(handler)
        try:
            response = self.client.get(path)
            for logger in loggers:
                logger.warning("Rejected request: %s", request.get_full_path(), extra={"request": request})
        finally:
            for logger in loggers:
                logger.removeHandler(handler)

        self.assertEqual(response.status_code, 404)
        output = stream.getvalue()
        for secret in (setup_secret, query_secret, "header-secret"):
            self.assertNotIn(secret, output)
        self.assertIn("/connect/<redacted>/", output)
        self.assertIn(setup_secret, request.path)
        self.assertEqual(request.META["HTTP_AUTHORIZATION"], "Bearer header-secret")

    def test_liveness_does_not_require_database_and_readiness_fails_safely(self):
        with patch("gateway.views.connection.cursor", side_effect=RuntimeError("database password leaked")):
            live = self.client.get("/health/live/")
            ready = self.client.get("/health/ready/")

        self.assertEqual(live.status_code, 200)
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json(), {"status": "unhealthy"})
        self.assertNotContains(ready, "password leaked", status_code=503)


class ConcurrentRateLimitTests(TransactionTestCase):
    def test_atomic_increment_has_no_lost_updates(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite concurrency regression test")
        workers = 4
        barrier = threading.Barrier(workers)

        def increment(_index):
            close_old_connections()
            try:
                barrier.wait()
                check_rate_limit("login", "concurrent-client", now=300)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(increment, range(workers)))

        self.assertEqual(RateLimitBucket.objects.get(scope="login").count, workers)
