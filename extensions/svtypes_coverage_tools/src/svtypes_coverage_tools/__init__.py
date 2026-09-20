"""Read-only coverage scanning, cataloging, viewing, and proposal utilities."""

from .catalog import CATALOG_FORMAT, CatalogError, build_catalog, load_catalog, write_catalog
from .proposal import PROPOSAL_FORMAT, ProposalError, apply_proposal, create_proposal, load_proposal, render_review, write_proposal
from .gui import GuiError, GuiSession, create_gui_server, start_gui_server
from .scanner import ScanError, preview_coverage_layout, scan_design

__all__ = [
    "CATALOG_FORMAT",
    "CatalogError",
    "PROPOSAL_FORMAT",
    "ProposalError",
    "GuiError",
    "GuiSession",
    "ScanError",
    "build_catalog",
    "create_proposal",
    "create_gui_server",
    "load_catalog",
    "load_proposal",
    "render_review",
    "scan_design",
    "preview_coverage_layout",
    "write_catalog",
    "write_proposal",
    "apply_proposal",
    "start_gui_server",
]
