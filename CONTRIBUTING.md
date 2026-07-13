# Contributing

Keep changes small, scoped, and dependency-free unless a dependency is explicitly approved.

Before submitting a change, run:

```sh
python3 scripts/validate_package.py
python3 -m unittest discover -s tests -v
git diff --check
```

Do not include secrets, generated caches, or unrelated formatting changes.
