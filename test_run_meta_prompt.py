"""Offline regression tests for the backend function and CLI."""

import contextlib
import io
import math
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx2 as httpx
from openai import APITimeoutError, OpenAI

import run_meta_prompt as runner


def fake_client(**response_fields):
    client = MagicMock(spec=OpenAI)
    client.responses = MagicMock()
    client.responses.create.return_value = SimpleNamespace(
        **{"status": "completed", "output_text": "优化后的提示词", "output": [], **response_fields}
    )
    return client


class OptimizePromptTests(unittest.TestCase):
    def test_preserves_input_and_uses_system_instructions(self):
        client = fake_client()
        prompt = "  请检查 {{code}}\n"
        with patch.object(runner, "OpenAI") as constructor:
            result = runner.optimize_prompt(prompt, model="test-model", client=client)
        self.assertEqual(result, "优化后的提示词")
        constructor.assert_not_called()
        client.responses.create.assert_called_once_with(
            model="test-model", instructions=runner.meta_prompt, input=prompt, timeout=60.0
        )
        client.close.assert_not_called()
        client.__exit__.assert_not_called()

    def test_invalid_inputs_fail_before_creating_client(self):
        invalid_values = (None, 123, [], "", " \n\t")
        for field in ("prompt", "model"):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    kwargs = {"prompt": "input", "model": "test-model", field: value}
                    with patch.object(runner, "OpenAI") as constructor:
                        with self.assertRaises(ValueError):
                            runner.optimize_prompt(**kwargs)
                    constructor.assert_not_called()

    def test_invalid_timeout_fails_before_api_call(self):
        client = fake_client()
        for timeout in (None, "60", True, False, 0, -1, math.inf, -math.inf, math.nan):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    runner.optimize_prompt("input", model="test-model", client=client, timeout=timeout)
        client.responses.create.assert_not_called()

    def test_custom_timeout(self):
        client = fake_client()
        runner.optimize_prompt("input", model="test-model", client=client, timeout=5)
        self.assertEqual(client.responses.create.call_args.kwargs["timeout"], 5)

    def test_owned_client_is_closed(self):
        client = fake_client()
        client.__enter__.return_value = client
        with patch.object(runner, "OpenAI", return_value=client):
            self.assertEqual(runner.optimize_prompt("input", model="test-model"), "优化后的提示词")
        client.__exit__.assert_called_once()

    def test_sdk_errors_propagate_and_owned_client_closes(self):
        client = fake_client()
        client.__enter__.return_value = client
        error = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        client.responses.create.side_effect = error
        with patch.object(runner, "OpenAI", return_value=client):
            with self.assertRaises(APITimeoutError) as raised:
                runner.optimize_prompt("input", model="test-model")
        self.assertIs(raised.exception, error)
        client.__exit__.assert_called_once()

    def test_incomplete_and_failed_responses_are_rejected(self):
        for status in ("incomplete", "failed", "cancelled", "queued", "in_progress", None):
            with self.subTest(status=status):
                client = fake_client(status=status, output_text="partial text")
                with self.assertRaises(RuntimeError):
                    runner.optimize_prompt("input", model="test-model", client=client)

    def test_empty_output_is_rejected(self):
        for output in ("", " \n\t"):
            with self.subTest(output=output):
                with self.assertRaises(RuntimeError):
                    runner.optimize_prompt("input", model="test-model", client=fake_client(output_text=output))

    def test_refusal_is_rejected_even_with_partial_text(self):
        output = [SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")])]
        with self.assertRaises(RuntimeError):
            runner.optimize_prompt("input", model="test-model", client=fake_client(output=output))

    def test_sdk_response_with_mock_http_transport(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={
                "id": "resp_test", "object": "response", "created_at": 0,
                "status": "completed", "model": "test-model",
                "output": [{"id": "msg_test", "type": "message", "role": "assistant",
                            "status": "completed", "content": [{"type": "output_text",
                            "text": "优化后的提示词", "annotations": []}]}],
            })

        with OpenAI(api_key="offline-test", http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
            result = runner.optimize_prompt("input", model="test-model", client=client, timeout=5)
            self.assertFalse(client.is_closed())
        self.assertEqual(result, "优化后的提示词")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].extensions["timeout"]["read"], 5)


class CliTests(unittest.TestCase):
    def test_existing_cli_arguments_keep_working(self):
        client = fake_client()
        client.__enter__.return_value = client
        with patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test"}):
            with patch.object(runner, "OpenAI", return_value=client):
                with patch("sys.argv", ["run_meta_prompt.py", "input", "--model", "test-model"]):
                    with patch("sys.stdout"), patch("builtins.print") as output:
                        runner.main()
        self.assertEqual(client.responses.create.call_args.kwargs["input"], "input")
        self.assertEqual(client.responses.create.call_args.kwargs["model"], "test-model")
        output.assert_called_once_with("优化后的提示词")

    def test_success_with_redirected_stdout(self):
        output = io.StringIO()
        with patch.object(runner, "optimize_prompt", return_value="优化后的提示词") as optimize:
            with contextlib.redirect_stdout(output):
                code = runner.main(["input", "--model", "test-model", "--timeout", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), "优化后的提示词\n")
        optimize.assert_called_once_with("input", model="test-model", timeout=5)

    def test_invalid_input_has_argparse_exit_code(self):
        with patch.object(runner, "optimize_prompt", side_effect=ValueError("prompt must not be empty")):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    runner.main([" ", "--model", "test-model"])
        self.assertEqual(raised.exception.code, 2)

    def test_sdk_error_does_not_expose_details(self):
        output, errors = io.StringIO(), io.StringIO()
        error = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        error.args = ("secret-api-key and sensitive prompt",)
        with patch.object(runner, "optimize_prompt", side_effect=error):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = runner.main(["input", "--model", "test-model"])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("APITimeoutError", errors.getvalue())
        self.assertNotIn("secret-api-key", errors.getvalue())
        self.assertNotIn("sensitive prompt", errors.getvalue())

    def test_invalid_response_has_nonzero_exit_code(self):
        errors = io.StringIO()
        with patch.object(runner, "optimize_prompt", side_effect=RuntimeError("Response is incomplete")):
            with contextlib.redirect_stderr(errors):
                code = runner.main(["input", "--model", "test-model"])
        self.assertEqual(code, 1)
        self.assertIn("Response is incomplete", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
