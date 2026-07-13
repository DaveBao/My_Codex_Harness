# Vendored grill skills

## Source and scope

- Repository: https://github.com/mattpocock/skills.git
- Release tag: `v1.1.0`
- Resolved commit: `d574778f94cf620fcc8ce741584093bc650a61d3`
- License: MIT

Only the upstream `grill-me` and `grilling` skills are included. Their pinned
`SKILL.md` files reference no relative assets, scripts, or supporting files,
so no unrelated upstream skills or resources are vendored. The exact upstream
`grill-me` file is preserved at `skills/grill-me/upstream/SKILL.md`; the active
top-level file is a local Codex-compatible wrapper because Codex rejects the
upstream `disable-model-invocation: true` field and uses `$grilling` invocation.

## File inventory

| Local path | SHA-256 | Upstream path | Status |
| --- | --- | --- | --- |
| `skills/grill-me/LICENSE.upstream` | `0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5` | `LICENSE` | Unmodified upstream file |
| `skills/grill-me/SKILL.md` | `55be62cba5804524524adfe5eb85c4f11513f0b4d89c26f162c6014c119a1fec` | Not applicable | Local Codex compatibility wrapper |
| `skills/grill-me/UPSTREAM.md` | `48e09798e661946d5d242700c978ee2d7672643a2f4dfc23185db3864080fd1b` | Not applicable | Local provenance companion |
| `skills/grill-me/upstream/SKILL.md` | `6189dfceb7304a6e5558f75d87e68fa3bc7fcf7ba120e44f21f8a61fe01eba54` | `skills/productivity/grill-me/SKILL.md` | Unmodified upstream file |
| `skills/grilling/LICENSE.upstream` | `0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5` | `LICENSE` | Unmodified upstream file |
| `skills/grilling/SKILL.md` | `5a35925d03a391bcfa46940868b649b72dba89ec9c19525e785bbb6bd3a7f478` | `skills/productivity/grilling/SKILL.md` | Unmodified upstream file |
| `skills/grilling/UPSTREAM.md` | `513b54dd6b01e2e2dcd5ec70a8861c7c6395d2032c3107b551d0af889e4f8757` | Not applicable | Local provenance companion |

The active `grill-me/SKILL.md` wrapper and two `UPSTREAM.md` files are local
additions. `NOTICE` and this document are also local attribution changes. All
other inventoried files are byte-for-byte copies from the pinned checkout.

## Update procedure

1. Resolve the requested tag independently with `git ls-remote --tags` and
   require its peeled commit to match the intended pin.
2. Check out that exact commit in a temporary clone. Inspect both `SKILL.md`
   files for relative references and add only directly required files.
3. Replace `grill-me/upstream/SKILL.md`, `grilling/SKILL.md`, and each
   `LICENSE.upstream` with exact copies from the verified checkout. Do not edit
   those upstream files locally.
4. Keep the active `grill-me/SKILL.md` wrapper limited to Codex invocation
   compatibility. Update the directory-local provenance companions, this
   inventory, and `NOTICE`. Recalculate every inventoried SHA-256.
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
git diff --check
```

The upstream MIT license and copyright notice are preserved verbatim in
`LICENSE.upstream` inside each vendored skill directory.
