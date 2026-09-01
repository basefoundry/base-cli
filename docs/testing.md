# Validation commands

The Base manifest declares `./tests/full_validate.sh` as the authoritative
test command. It runs the repository baseline checks, Python tests with the
coverage policy, strict typing, formatting and lint checks, schema and
contract validation, documentation checks, compatibility-dashboard and
performance checks, and the available security gates.

Run it from a clean checkout after installing the development and quality
extras:

```bash
python -m pip install '.[dev,typer,quality]'
./tests/full_validate.sh
```

`./tests/validate.sh` remains the fast repository-baseline check used when
dependencies are not yet installed. It is not a substitute for the full
validation gate.
