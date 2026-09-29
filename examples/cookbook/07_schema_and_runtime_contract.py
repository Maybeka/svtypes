"""Cookbook 第 7 章：公开模型的 schema identity 与运行时要求。"""

from svtypes import Bit, RemoteRef, RuntimeCapabilities, SvObject, require_runtime_compatible, runtime_capabilities, schema_descriptor


class Command(SvObject):
    opcode = Bit[8]()
    # RemoteRef 是跨边界身份值，不会递归打包本地对象图。
    destination = RemoteRef["demo.Device"]()


def main() -> None:
    # descriptor 的两个 fingerprint 分别刻画声明与编码；发布/握手时应记录它们。
    descriptor = schema_descriptor(Command)
    local = runtime_capabilities()
    peer = RuntimeCapabilities(provided=("future.feature", *local.provided))

    # 对端可额外声明未来能力；但所需能力与格式版本必须在传输前相容。
    require_runtime_compatible(("svtypes.codec-context.v1",), peer)

    assert descriptor.unified_type_name.endswith(".Command")
    assert descriptor.schema_fingerprint
    assert descriptor.encoding_fingerprint
    print("类型：", descriptor.unified_type_name)
    print("schema fingerprint：", descriptor.schema_fingerprint.hex())
    print("已提供能力：", ", ".join(local.provided))


if __name__ == "__main__":
    main()
