import unittest


class RecoveryRunnerTest(unittest.TestCase):
    def test_builds_fixed_podman_compose_command_without_shell(self):
        from mcp_servers.recovery.podman_runner import build_rollback_command

        command = build_rollback_command("v1")
        self.assertIsInstance(command, list)
        self.assertIn("python", command[0])
        self.assertIn("podman_compose", command)
        self.assertNotIn("shell=True", " ".join(command))

    def test_rejects_non_allowlisted_version(self):
        from mcp_servers.recovery.podman_runner import build_rollback_command

        with self.assertRaises(ValueError):
            build_rollback_command("latest")


if __name__ == "__main__":
    unittest.main()
