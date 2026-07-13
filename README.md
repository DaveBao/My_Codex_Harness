# My Codex Harness

My Codex Harness packages a small, reusable engineering workflow for Codex. The repository is the source for the `my-codex-harness` plugin and its public marketplace metadata.

## Validate the package

The validator uses only the Python standard library:

```sh
python3 scripts/validate_package.py
python3 -m unittest discover -s tests -v
```

Source: <https://github.com/DaveBao/My_Codex_Harness>
