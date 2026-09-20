# SvTypes coverage tools

Independent tools for scanning, cataloging, interactively exploring, and
proposing changes to SvTypes coverage declarations. They consume Design
Manifest data only; they do not sample, randomize, or read coverage databases.

E2 commands:

```sh
svtypes-coverage scan my_design.packet --out packet.design.json
svtypes-coverage catalog --input packet.design.json --out packet.catalog.json
svtypes-coverage gui --source-root . --python-path . --module my_design.packet
svtypes-coverage proposal create --catalog refreshed.catalog.json --covergroup Packet::cg --operation '{...}' --edit '{...}' --rationale '...' --out suggested-change.json
svtypes-coverage proposal review --proposal suggested-change.json
svtypes-coverage proposal apply --proposal suggested-change.json --catalog refreshed.catalog.json --source-root . --confirm
```

`scan` imports each module entry in a dedicated child Python process and
discovers every `SvObject` subclass defined in that module. Pass
`--python-path` explicitly when the design is not installed in that process.

`gui` starts a persistent loopback-only design GUI. It can scan/refresh a
module entry, browse the static type/field hierarchy and each type's
covergroup declarations, inspect bin selectors (array-bin members can be
expanded in place), filter declarations, and preview a `CoverInput` instance
layout. Module-defined types appear as sibling roots; a field reference is an
edge under its owner, not a reason to hide the target type. The GUI is
read-only: it does not create proposals, read a coverage database, or display
sampling results. Coverpoint pages may include an in-page domain editor
(sliders stay in the browser; they do not write source). See
`docs/SVTYPES_COVERAGE_GUI_DOMAIN_EDITOR.md`.

Layout preview materializes bins from a covergroup's `CoverInput` signature
and optional `Parameter` actuals. It does not read `@coverage_init` actuals.

The separate `proposal` CLI commands remain available. `proposal apply` applies
only literal edits already contained in a reviewed
proposal, and only after its covergroup digest and expected source text match a
freshly generated catalog.
