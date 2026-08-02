from __future__ import annotations

import unittest

import click

from base_cli.history import redact_history_argv
from base_cli.redaction import (
    REDACTED,
    RedactionPlan,
    compile_redaction_plan,
    option_aliases_from_decls,
    parameter_name_from_decls,
    redact_argv,
)


def _sensitive(parameter: object) -> object:
    setattr(parameter, "_base_cli_sensitive", True)
    return parameter


def _command_tree() -> click.Group:
    root_token = _sensitive(click.Option(["--root-token"]))
    verbose = click.Option(["-v", "--verbose"], is_flag=True)
    token = _sensitive(click.Option(["-t", "--token", "credential"]))
    pair = _sensitive(click.Option(["--credential-pair"], nargs=2))
    api_key = click.Option(["--api-key"])
    split_flag = _sensitive(click.Option(["--auth/--no-auth"], is_flag=True))
    payload = _sensitive(click.Argument(["payload"]))
    label = click.Argument(["label"], required=False)
    push = click.Command(
        "push",
        params=[verbose, token, pair, api_key, split_flag, payload, label],
    )
    return click.Group("tool", params=[root_token], commands={"push": push})


class DeclarationTests(unittest.TestCase):
    def test_explicit_destination_precedes_alias_derived_name(self) -> None:
        self.assertEqual(
            parameter_name_from_decls(("-t", "--token", "credential")),
            "credential",
        )

    def test_alias_collection_includes_both_boolean_forms(self) -> None:
        self.assertEqual(
            option_aliases_from_decls(("-a", "--auth/--no-auth", "authorization_mode")),
            ("-a", "--auth", "--no-auth"),
        )

    def test_destination_derivation_ignores_secondary_boolean_alias(self) -> None:
        self.assertEqual(parameter_name_from_decls(("-x/--no-foo",)), "x")


class LegacySetRedactionTests(unittest.TestCase):
    def test_raw_aliases_and_destination_names_are_supported(self) -> None:
        cases = (
            (["tool", "--token", "long"], {"token"}, ["tool", "--token", REDACTED]),
            (["tool", "--credential=long"], {"--credential"}, ["tool", f"--credential={REDACTED}"]),
            (["tool", "-p", "short"], {"-p"}, ["tool", "-p", REDACTED]),
            (["tool", "-pshort"], {"p"}, ["tool", f"-p{REDACTED}"]),
            (["tool", "-p=short"], {"-p"}, ["tool", f"-p={REDACTED}"]),
            (["tool", "+pplus"], {"+p"}, ["tool", f"+p{REDACTED}"]),
            (["tool", "/pslash"], {"/p"}, ["tool", f"/p{REDACTED}"]),
        )
        for argv, sensitive, expected in cases:
            with self.subTest(argv=argv, sensitive=sensitive):
                self.assertEqual(redact_argv(argv, sensitive), expected)

    def test_secret_name_heuristics_apply_without_registration(self) -> None:
        argv = [
            "tool",
            "--password",
            "hunter2",
            "API_TOKEN=value",
            "https://user:pass@example.test/path",
        ]
        expected = [
            "tool",
            "--password",
            REDACTED,
            f"API_TOKEN={REDACTED}",
            f"https://{REDACTED}@example.test/path",
        ]

        self.assertEqual(redact_argv(argv, set()), expected)
        self.assertEqual(redact_history_argv(argv, set()), expected)

    def test_option_looking_values_follow_click_consumption(self) -> None:
        self.assertEqual(
            redact_argv(["tool", "--token", "--verbose"], {"token"}),
            ["tool", "--token", REDACTED],
        )


class RedactionPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = compile_redaction_plan(_command_tree())

    def test_plan_is_set_compatible_and_contains_destinations_and_aliases(self) -> None:
        self.assertIsInstance(self.plan, set)
        self.assertIsInstance(self.plan, RedactionPlan)
        self.assertTrue(
            {"credential", "-t", "--token", "root_token", "--root-token"}.issubset(self.plan)
        )
        self.assertTrue({"--auth", "--no-auth"}.issubset(self.plan))

    def test_recursive_plan_redacts_option_forms_and_positional_span(self) -> None:
        cases = (
            (
                ["tool", "--root-token=root", "push", "--token", "value", "payload", "visible"],
                ["tool", f"--root-token={REDACTED}", "push", "--token", REDACTED, REDACTED, "visible"],
            ),
            (
                ["tool", "push", "-vtattached", "payload", "visible"],
                ["tool", "push", f"-vt{REDACTED}", REDACTED, "visible"],
            ),
            (
                ["tool", "push", "--credential-pair=first", "second", "payload"],
                ["tool", "push", f"--credential-pair={REDACTED}", REDACTED, REDACTED],
            ),
            (
                ["tool", "push", "--api-key", "automatic", "payload"],
                ["tool", "push", "--api-key", REDACTED, REDACTED],
            ),
            (
                ["tool", "push", "--", "--literal-payload", "visible"],
                ["tool", "push", "--", REDACTED, "visible"],
            ),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(redact_argv(argv, self.plan), expected)
                self.assertEqual(redact_history_argv(argv, self.plan), expected)

    def test_sensitive_positional_is_redacted_by_span_not_value_matching(self) -> None:
        self.assertEqual(
            redact_argv(["tool", "push", "same", "same"], self.plan),
            ["tool", "push", REDACTED, "same"],
        )

    def test_option_looking_sensitive_value_follows_click_consumption(self) -> None:
        self.assertEqual(
            redact_argv(["tool", "push", "--token", "--verbose", "payload"], self.plan),
            ["tool", "push", "--token", REDACTED, REDACTED],
        )

    def test_malformed_argv_table_is_total_and_never_drops_tokens(self) -> None:
        cases = (
            (["tool", "push", "--token"], ["tool", "push", "--token"]),
            (["tool", "push", "--token", "--verbose"], ["tool", "push", "--token", REDACTED]),
            (["tool", "push", "--token", "--unknown"], ["tool", "push", "--token", REDACTED]),
            (["tool", "push", "--token", "--"], ["tool", "push", "--token", REDACTED]),
            (["tool", "push", "--token="], ["tool", "push", f"--token={REDACTED}"]),
            (["tool", "push", "-t"], ["tool", "push", "-t"]),
            (["tool", "push", "-t", "-v"], ["tool", "push", "-t", REDACTED]),
            (["tool", "push", "-tvalue"], ["tool", "push", f"-t{REDACTED}"]),
            (["tool", "push", "--", "--token", "raw"], ["tool", "push", "--", REDACTED, "raw"]),
            (["tool", "unknown", "--token", "raw"], ["tool", "unknown", "--token", "raw"]),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                actual = redact_argv(argv, self.plan)
                self.assertEqual(actual, expected)
                self.assertEqual(len(actual), len(argv))

    def test_token_normalization_applies_to_commands_and_option_forms(self) -> None:
        token = _sensitive(click.Option(["-t", "--token"]))
        push = click.Command("push", params=[token])
        root = click.Group(
            "tool",
            commands={"push": push},
            context_settings={"token_normalize_func": str.lower},
        )
        plan = compile_redaction_plan(root)

        self.assertEqual(
            redact_argv(["tool", "PUSH", "--TOKEN", "long"], plan),
            ["tool", "PUSH", "--TOKEN", REDACTED],
        )
        self.assertEqual(
            redact_argv(["tool", "PUSH", "-Tattached"], plan),
            ["tool", "PUSH", f"-T{REDACTED}"],
        )

    def test_context_parser_settings_control_sensitive_positional_spans(self) -> None:
        password = _sensitive(click.Argument(["password"]))
        unknown_command = click.Command(
            "probe",
            params=[password],
            context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
        )
        self.assertEqual(
            redact_argv(["probe", "-hunter2"], compile_redaction_plan(unknown_command)),
            ["probe", REDACTED],
        )

        verbose = click.Option(["-v", "+v"], is_flag=True)
        mixed_command = click.Command(
            "probe",
            params=[verbose, password],
            context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
        )
        for cluster in ("-xv", "-vx", "-xyv", "+xv"):
            with self.subTest(cluster=cluster):
                self.assertEqual(
                    redact_argv(["probe", cluster], compile_redaction_plan(mixed_command)),
                    ["probe", REDACTED],
                )

        verbose = click.Option(["--verbose"], is_flag=True)
        first = click.Argument(["first"])
        stopped_command = click.Command(
            "probe",
            params=[verbose, first, password],
            context_settings={"allow_interspersed_args": False},
        )
        self.assertEqual(
            redact_argv(["probe", "visible", "--verbose"], compile_redaction_plan(stopped_command)),
            ["probe", "visible", REDACTED],
        )

        bang_verbose = click.Option(["!v"], is_flag=True)
        punctuation_command = click.Command(
            "probe",
            params=[bang_verbose, first, password],
            context_settings={
                "ignore_unknown_options": True,
                "allow_extra_args": True,
                "allow_interspersed_args": False,
            },
        )
        self.assertEqual(
            redact_argv(
                ["probe", "!x", "!v", "supersecret"],
                compile_redaction_plan(punctuation_command),
            ),
            ["probe", "!x", "!v", REDACTED],
        )

    def test_variadic_argument_backfill_protects_following_sensitive_argument(self) -> None:
        sources = click.Argument(["sources"], nargs=-1)
        password = _sensitive(click.Argument(["password"]))
        command = click.Command("probe", params=[sources, password])

        self.assertEqual(
            redact_argv(["probe", "one", "secret"], compile_redaction_plan(command)),
            ["probe", "one", REDACTED],
        )

    def test_duplicate_normalized_aliases_union_sensitive_markers(self) -> None:
        sensitive = _sensitive(click.Option(["--credential", "secret_dest"]))
        plain = click.Option(["--credential", "plain_dest"])
        command = click.Command("probe", params=[sensitive, plain])

        self.assertEqual(
            redact_argv(
                ["probe", "--credential", "duplicate-secret"],
                compile_redaction_plan(command),
            ),
            ["probe", "--credential", REDACTED],
        )


if __name__ == "__main__":
    unittest.main()
