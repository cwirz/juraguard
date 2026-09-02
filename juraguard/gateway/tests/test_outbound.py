from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from gateway.outbound import OutboundError, request


class OutboundRequestTests(SimpleTestCase):
    @patch("gateway.outbound.time.monotonic", side_effect=[100, 101, 102, 103, 109, 111])
    @patch("gateway.outbound._connection")
    @patch("gateway.outbound.resolve_remote_target")
    def test_drip_fed_response_cannot_extend_total_deadline(self, resolve, connection_factory, _monotonic):
        resolve.return_value.path = "/resource"
        connection = connection_factory.return_value
        response = MagicMock()
        response.status = 200
        response.getheaders.return_value = []
        response.read1.side_effect = [b"first", b"second"]
        connection.getresponse.return_value = response

        with self.assertRaisesMessage(OutboundError, "timed out"):
            request("https://example.com/resource")

        self.assertEqual(connection.sock.settimeout.call_args_list[0].args, (9,))
        self.assertEqual(connection.sock.settimeout.call_args_list[-1].args, (1,))
