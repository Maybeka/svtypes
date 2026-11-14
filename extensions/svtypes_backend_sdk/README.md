# SvTypes backend SDK (E1)

This package is an independent consumer of `svtypes.design-manifest/v1`.
It discovers a backend only when explicitly requested, freezes the input,
validates capabilities, and writes deterministic artifacts without modifying
SvTypes or its existing generators.

```sh
svtypes-backend build --backend svtypes_backend_sdk.reference:backend \
  --input packet.design.json --out generated/
```

Backends provide `metadata`, `validate(design)` and `render(design, options)`.
They receive immutable JSON-shaped data and return an `ArtifactSet`.  The
reference backend produces `coverage-reference.json`; it demonstrates the
protocol and is not an alternative SV renderer.

Publication preserves files outside the preceding artifact manifest. A managed
file edited since generation, or an unmanaged file at a new artifact path,
causes the entire publication to fail before changing the destination. To
explicitly replace those conflicting targets, use `--overwrite-modified` (or
`build(..., overwrite_modified=True)`). This never clears unrelated files.
Unchanged obsolete artifacts are removed; edited obsolete artifacts are kept.
Symlink traversal, case-insensitive path collisions, Windows drive/alternate
stream paths and the reserved manifest path are rejected. A failed publication
restores the previous directory.
