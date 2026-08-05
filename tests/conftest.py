"""Platform-specific collection policy for the cross-platform test matrix."""

from __future__ import annotations

import os

import pytest


_WINDOWS_SKIP_MODULES = frozenset(
    {
        "tests/test_app_run_metadata.py",
        "tests/test_cleanup_security.py",
        "tests/test_invocation_parity.py",
        "tests/test_run_bundle_retention.py",
    }
)
_WINDOWS_SKIP_TESTS = frozenset(
    {
        "tests/test_app_runtime_errors.py::AppRuntimeErrorTests::test_run_app_reports_unusable_cache_root_without_traceback",
        "tests/test_app_security_boundaries.py::AppCleanupBoundaryTests::test_successful_inherited_invocation_preserves_parent_bundle",
        "tests/test_app_startup_transaction.py::AppStartupTransactionTests::test_post_logger_failure_erases_owned_temp_through_retained_handle",
        "tests/test_app_startup_transaction.py::AppStartupTransactionTests::test_startup_rollback_never_unlinks_log_through_a_swapped_symlink_ancestor",
        "tests/test_explicit_config_validation.py::ExplicitConfigValidationTests::test_missing_explicit_config_is_rejected_before_profile_or_runtime_startup",
        "tests/test_explicit_config_validation.py::ExplicitConfigValidationTests::test_unreadable_explicit_config_is_rejected_before_profile_or_runtime_startup",
        "tests/test_generic_core.py::GenericCoreTests::test_history_writer_requires_consumer_selected_path",
        "tests/test_logging.py::ConfigureLoggerTests::test_configure_logger_creates_log_file_restrictively_before_post_create_chmod",
        "tests/test_logging.py::ConfigureLoggerTests::test_configure_logger_keeps_persistent_logs_plain_when_user_stream_is_colored",
        "tests/test_logging.py::ConfigureLoggerTests::test_configure_logger_opens_log_file_without_fchmod",
        "tests/test_logging.py::ConfigureLoggerTests::test_configure_logger_uses_custom_formatter_for_file_handler",
    }
)


def pytest_collection_modifyitems(
    session: pytest.Session,
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    del session, config
    if os.name != "nt":
        return
    marker = pytest.mark.skip(reason="requires POSIX filesystem semantics")
    for item in items:
        base_nodeid = item.nodeid.split("[", 1)[0]
        module = base_nodeid.split("::", 1)[0]
        if module in _WINDOWS_SKIP_MODULES or base_nodeid in _WINDOWS_SKIP_TESTS:
            item.add_marker(marker)
