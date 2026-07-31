# Changelog

All notable changes to base-cli will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions are tracked in the repo-root `VERSION` file.

## [Unreleased]

### Changed

- Make `base_cli.App()` use the consumer-neutral profile by default; the
  temporary Base compatibility profile is now explicit.

### Added

- Initialized the repository with the Base-managed repo baseline.
- Added the guarded package build, artifact validation, and protected
  TestPyPI/PyPI publication workflow.
- Exposed `base_cli.__version__` from the repository and installed package
  version contract.
- Pinned the build backend to metadata compatible with the bundled publication
  action and made license-file validation portable across setuptools versions.
