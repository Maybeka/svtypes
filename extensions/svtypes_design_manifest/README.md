# SvTypes Design Manifest bridge (E0 prototype)

This independently packaged bridge exports a read-only, deterministic
`svtypes.design-manifest/v1` document from explicitly supplied SvTypes types.
It is an extension consumer: it does not modify SvTypes generation, coverage
evaluation, randomization, or coverage databases.

Install it alongside a compatible SvTypes distribution, then export explicit
type entry points:

```sh
svtypes-design-export my_design.packet:Packet --out packet.design.json
```

The command imports the specified module.  Invoke it only for a design that is
safe to import.  The later coverage scanner will run imports in a dedicated
isolated process; this E0 bridge intentionally provides only explicit export.

The JSON Schema is shipped at
`svtypes_design_manifest/schemas/design-manifest-v1.schema.json`.  Use
`export_design_manifest()` for in-process integration and
`validate_design_manifest()` for structural validation of saved documents.

`design.manifest_digest` covers semantic design and coverage declarations.
Presentation metadata such as source declaration order is emitted separately
under `presentation` and does not change that digest.
