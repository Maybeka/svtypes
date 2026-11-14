"""Public ownership policy of the Python identity registry."""

from svtypes import CodecSession, PackContext, UnpackContext


session = CodecSession(retain_objects=False)
retains_objects: bool = session.retain_objects
context: PackContext = session.pack_context()
unpack_context: UnpackContext = session.unpack_context()
