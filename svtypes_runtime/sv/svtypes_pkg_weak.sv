// Experimental IEEE 1800-2023 weak-reference variant of svtypes_pkg.
// Select this file instead of svtypes_pkg.sv; do not compile both.
`define SVTYPES_USE_WEAK_REFERENCE
`include "svtypes_pkg.sv"
`undef SVTYPES_USE_WEAK_REFERENCE
