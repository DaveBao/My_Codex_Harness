# Vendored Matt Pocock skills

## Source and scope

- Repository: https://github.com/mattpocock/skills.git
- Release tag: `v1.1.0`
- Resolved commit: `d574778f94cf620fcc8ce741584093bc650a61d3`
- License: MIT

Only the upstream `grill-me`, `grilling`, and `to-spec` skills are included.
Their pinned `SKILL.md` files reference no required relative assets or scripts,
so no unrelated upstream skills or resources are vendored. Exact upstream files
are preserved under each skill's `upstream/SKILL.md`. The active top-level files
are local Owner-gated Codex/Harness adapters that omit the unsupported upstream
`disable-model-invocation: true` field.

## File inventory

| Local path | SHA-256 | Upstream path | Status |
| --- | --- | --- | --- |
| `skills/grill-me/LICENSE.upstream` | `0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5` | `LICENSE` | Unmodified upstream file |
| `skills/grill-me/SKILL.md` | `b6c16fe9efd26d7fdc032ec0f1ea9c71940c221aaf3d9d529872e2d1b7f6170e` | Not applicable | Local Owner-gated Codex adapter |
| `skills/grill-me/UPSTREAM.md` | `48e09798e661946d5d242700c978ee2d7672643a2f4dfc23185db3864080fd1b` | Not applicable | Local provenance companion |
| `skills/grill-me/agents/openai.yaml` | `7e1b51f90869dd7fb9de86be382198faecfdbea71ecb9809d158b90174e44c21` | Not applicable | Local Codex skill metadata |
| `skills/grill-me/upstream/SKILL.md` | `6189dfceb7304a6e5558f75d87e68fa3bc7fcf7ba120e44f21f8a61fe01eba54` | `skills/productivity/grill-me/SKILL.md` | Unmodified upstream file |
| `skills/grilling/LICENSE.upstream` | `0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5` | `LICENSE` | Unmodified upstream file |
| `skills/grilling/SKILL.md` | `f8cb6e0267503d471db1459124c7a15036b05d831abc2c7482c28fe4b0f48368` | Not applicable | Local Owner-gated Codex adapter |
| `skills/grilling/UPSTREAM.md` | `57210b403fda80c1c6dfa10fd9364c69400209a8abb57984246994524bd6ebb2` | Not applicable | Local provenance companion |
| `skills/grilling/agents/openai.yaml` | `bd8445e71a0ecb96301e25182943cfa20f641345d50ea6b30dc2293b690fb678` | Not applicable | Local Codex skill metadata |
| `skills/grilling/upstream/SKILL.md` | `5a35925d03a391bcfa46940868b649b72dba89ec9c19525e785bbb6bd3a7f478` | `skills/productivity/grilling/SKILL.md` | Unmodified upstream file |
| `skills/to-spec/LICENSE.upstream` | `0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5` | `LICENSE` | Unmodified upstream file |
| `skills/to-spec/SKILL.md` | `4f8b51f3374eaef34188096572ac8843e0d0a4ab34ca37d508e947579b5616e2` | Not applicable | Local Owner-gated Harness adapter |
| `skills/to-spec/UPSTREAM.md` | `1b5e542fe47ff497d374dd18710345d225961f62adec6cb939723e9123a0e89c` | Not applicable | Local provenance companion |
| `skills/to-spec/agents/openai.yaml` | `dd58f260510578ef161625a100effa01af5561d8a08258bdeab2f91344e0c3fe` | Not applicable | Local Codex skill metadata |
| `skills/to-spec/upstream/SKILL.md` | `267638edd513b5918de626ad5605d261952abb7428cb308869c663ca924e93e7` | `skills/engineering/to-spec/SKILL.md` | Unmodified upstream file |

The three active `SKILL.md` adapters, three `UPSTREAM.md` files, and Codex
`agents/openai.yaml` metadata are local additions. `NOTICE` and this document
are also local attribution changes. Only the `upstream/SKILL.md` and
`LICENSE.upstream` files are byte-for-byte copies from the pinned checkout.

## Update procedure

1. Resolve the requested tag independently with `git ls-remote --tags` and
   require its peeled commit to match the intended pin.
2. Check out that exact commit in a temporary clone. Inspect every vendored `SKILL.md`
   files for relative references and add only directly required files.
3. Replace each `upstream/SKILL.md` and
   `LICENSE.upstream` with exact copies from the verified checkout. Do not edit
   those upstream files locally.
4. Keep active wrappers limited to explicit Owner activation and upstream
   delegation. Update the directory-local provenance companions, this inventory,
   and `NOTICE`. Recalculate every inventoried SHA-256.
5. For an intentional upstream upgrade, update the immutable
   `UPSTREAM_LICENSE_SHA256` and `UPSTREAM_SKILL_HASHES` test oracles in
   `tests/test_vendor.py` to the newly verified values. Never update an oracle
   merely to accept an unexplained local change.
6. Run all verification commands below before accepting the update.

## Verification commands

Pin check on POSIX or PowerShell:

```sh
git ls-remote --tags https://github.com/mattpocock/skills.git refs/tags/v1.1.0 'refs/tags/v1.1.0^{}'
```

### POSIX

```sh
codex_home="${CODEX_HOME:-$HOME/.codex}"
python3 scripts/validate_package.py
python3 -m unittest tests.test_vendor -v
python3 -m unittest discover -s tests -v
python3 "$codex_home/skills/.system/plugin-creator/scripts/validate_plugin.py" .
python3 "$codex_home/skills/.system/skill-creator/scripts/quick_validate.py" skills/grill-me
python3 "$codex_home/skills/.system/skill-creator/scripts/quick_validate.py" skills/grilling
python3 "$codex_home/skills/.system/skill-creator/scripts/quick_validate.py" skills/to-spec
git diff --check
```

### PowerShell

```powershell
$CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
py -3 scripts/validate_package.py
py -3 -m unittest tests.test_vendor -v
py -3 -m unittest discover -s tests -v
py -3 (Join-Path $CodexHome "skills/.system/plugin-creator/scripts/validate_plugin.py") .
py -3 (Join-Path $CodexHome "skills/.system/skill-creator/scripts/quick_validate.py") skills/grill-me
py -3 (Join-Path $CodexHome "skills/.system/skill-creator/scripts/quick_validate.py") skills/grilling
py -3 (Join-Path $CodexHome "skills/.system/skill-creator/scripts/quick_validate.py") skills/to-spec
git diff --check
```

The upstream MIT license and copyright notice are preserved verbatim in
`LICENSE.upstream` inside each vendored skill directory.
