# Workflows

Create a development environment with the repository's `uv` metadata and extras.
The Base manifest declares the dev, typer, quality, and benchmark extras for its
test path; `basectl setup base-cli` and `basectl test base-cli` use that contract
in a Base workspace. Generic consumers need only their declared package extras.

The authoritative development aggregate is `./tests/full_validate.sh`. Its
`--gate` options allow baseline, runtime, coverage, typing, style, contracts,
benchmark, and security checks to run independently. Documentation/API changes
should exercise the documentation and contract validators, not just syntax.
Use the testing guide for dependencies and the full validation matrix.

Work from an issue-backed dedicated worktree, preserve the primary checkout,
and link each PR to its issue. Record the actual checks run and distinguish
focused validation from the complete aggregate. Update CHANGELOG only for
release-worthy changes; documentation orientation does not change package
versions. Follow the release process for artifact publication rather than
publishing from a documentation PR.

Sources: [contribution guide](../CONTRIBUTING.md),
[validation driver](../tests/full_validate.sh), [testing](../docs/testing.md),
[Base manifest](../base_manifest.yaml), and [releasing](../docs/releasing.md).
