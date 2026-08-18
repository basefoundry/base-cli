from __future__ import annotations

import importlib
import unittest

import base_cli


class AppModuleBoundaryTests(unittest.TestCase):
    def test_public_app_imports_remain_stable_through_internal_boundaries(self) -> None:
        app_module = importlib.import_module("base_cli.app")
        core_module = importlib.import_module("base_cli._app_core")
        lifecycle_module = importlib.import_module("base_cli._lifecycle_install")
        attachment_module = importlib.import_module("base_cli._attach")
        run_module = importlib.import_module("base_cli._run")

        self.assertIs(app_module, core_module)
        self.assertIs(base_cli.App, core_module.App)
        self.assertIs(base_cli.run_app, run_module.run_app)
        self.assertIs(core_module._install_native_lifecycle_options, lifecycle_module._install_native_lifecycle_options)
        self.assertIs(
            core_module._instrument_attached_click_command, attachment_module._instrument_attached_click_command
        )
        self.assertIs(core_module._json_requested, run_module._json_requested)


if __name__ == "__main__":
    unittest.main()
