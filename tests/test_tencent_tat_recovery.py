import json
import unittest
from types import SimpleNamespace


class FakeTencentTatApi:
    def __init__(self, statuses: list[str | None]) -> None:
        self.statuses = iter(statuses)
        self.payload: dict[str, object] | None = None

    def invoke_command(self, payload: dict[str, object]) -> str:
        self.payload = payload
        return "inv-rollback-001"

    def describe_invocation(self, _: str) -> str | None:
        return next(self.statuses)


class TencentTatRecoveryTest(unittest.TestCase):
    def test_invokes_fixed_tat_command_and_waits_for_success(self):
        from oncall.execution.tencent_tat import (
            TencentTatConfig,
            invoke_tencent_tat_rollback,
        )

        api = FakeTencentTatApi(["PENDING", "SUCCESS"])
        config = TencentTatConfig(
            region="ap-guangzhou",
            command_id="cmd-rollback-release",
            instance_ids=["ins-001", "ins-002"],
            parameters={"namespace": "payments", "service": "checkout"},
            target_version_parameter="target",
            current_version_parameter="current",
        )
        result = invoke_tencent_tat_rollback(
            SimpleNamespace(current_version="2026.08.5", target_version="2026.08.4"),
            config,
            api=api,
            sleep=lambda _: None,
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["invocation_id"], "inv-rollback-001")
        self.assertEqual(api.payload["CommandId"], "cmd-rollback-release")
        self.assertEqual(api.payload["InstanceIds"], ["ins-001", "ins-002"])
        self.assertEqual(
            json.loads(str(api.payload["Parameters"])),
            {
                "namespace": "payments",
                "service": "checkout",
                "target": "2026.08.4",
                "current": "2026.08.5",
            },
        )

    def test_tat_failure_is_returned_without_reporting_success(self):
        from oncall.execution.tencent_tat import (
            TencentTatConfig,
            invoke_tencent_tat_rollback,
        )

        result = invoke_tencent_tat_rollback(
            SimpleNamespace(current_version="v2", target_version="v1"),
            TencentTatConfig(
                region="ap-guangzhou",
                command_id="cmd-rollback-release",
                instance_ids=["ins-001"],
            ),
            api=FakeTencentTatApi(["FAILED"]),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["invocation_status"], "FAILED")
