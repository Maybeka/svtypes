"""Strict Pyright regression fixture for SVX-facing type annotations."""

from svtypes import Bit, Int, Queue, RemoteRef


def transfer(
    opcode: Bit[8],
    payload: Queue[Bit[8]],
    peer: RemoteRef["sv://pkg/Driver"],
) -> Int:
    raise NotImplementedError
