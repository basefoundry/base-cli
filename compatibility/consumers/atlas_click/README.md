# Atlas consumer fixture

Atlas represents a team with an established Click group and a monitoring
script. The fixture proves that `base_cli.attach()` adds lifecycle behavior
without rebuilding the Click tree or changing the inventory output contract.

Install with `python -m pip install .`, run `atlas-consumer --help`, and execute
`atlas-consumer --quiet inventory`. The package is pinned to the supported
`base-cli` 0.3 minor window; tests run against the installed wheel in CI.
