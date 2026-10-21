---
title: Developers
nav_order: 6
has_children: true
---

# Developers

The plugin is three Python files with no dependencies beyond the standard
library and GIMP's GObject Introspection bindings.

| File | Role |
|---|---|
| `gimp-ai-plugin.py` | Procedure registration, dialogs, OpenAI and Bedrock calls, the GIMP image pipeline |
| `coordinate_utils.py` | Pure coordinate math - context windows, shapes, padding, scaling. No GIMP imports, unit tested |
| `bedrock_client.py` | SigV4 signing and Bedrock request building with `hmac`/`hashlib` - no boto3. No GIMP imports |

## In this section

- [Amazon Bedrock internals](bedrock.md) - models, API quirks, the bugs found
  while adding Bedrock and how each was verified
- [Image preparation algorithms](algorithms.md) - how images and masks are
  sized for each provider and placed back into GIMP
- [Release process](release.md) - automated releases from PR labels

## Tests

```bash
python3 bedrock_client.py              # signing, credentials, request bodies
python3 tests/run_tests.py             # coordinate transformations
python3 tests/test_native_context.py

# These run inside GIMP; some make live Bedrock calls
gimp -i --batch-interpreter=python-fu-eval \
     -b "import runpy; runpy.run_path('$PWD/tests/test_gimp_pipeline.py')"
```

See [Amazon Bedrock internals](bedrock.md#verification) for the full list and
what each covers.

## Tools

- **`build_release.py`** - creates the distributable ZIP (used by the release workflow)
- **`tools/bump_version.py`** - bumps `VERSION` in `gimp-ai-plugin.py` (used by the release workflow)

## Contributing

- Label PRs with `major`, `minor` or `patch`; merging to `main` bumps the
  version, tags it and publishes a GitHub release
- Update [CHANGELOG.md](https://github.com/etet100/gimp-ai-2/blob/main/CHANGELOG.md)
  for user-facing changes
- Keep coordinate logic in `coordinate_utils.py` and free of GIMP imports, and
  add tests for it

## This documentation

These pages are built by GitHub Pages from the `docs/` folder with the
[Just the Docs](https://just-the-docs.com) theme. Edit the Markdown and push to
`main`; links between pages are written as relative `.md` links so they also
work when browsing the repository on GitHub.
