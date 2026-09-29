"""Cookbook 第 1 章：声明一个事务，并完成二进制编码往返。"""

from svtypes import Array, Bit, Enum, Int, SvObject


class Opcode(Enum[Bit[2]]):
    # Enum 必须声明底层存储宽度；这会同时约束 Python、编码和生成代码。
    READ = 0
    WRITE = 1
    FLUSH = 2


class Header(SvObject):
    # 字段书写顺序就是稳定二进制布局的一部分，修改它等同于修改协议。
    address = Bit[16]()
    length = Bit[8]()
    opcode = Opcode()
    payload = Array[Bit[8], 4]()
    # rand=False 只是不参加默认随机化；该字段仍会被编码。
    retry_count = Int(rand=False)


def main() -> None:
    # 先像普通 Python 对象一样填写一笔协议数据。
    request = Header()
    request.address.value = 0x1234
    request.length.value = 4
    request.opcode.value = Opcode.WRITE
    request.payload.value = [0xDE, 0xAD, 0xBE, 0xEF]
    request.retry_count.value = 2

    # to_bytes() 产生可发送的完整对象编码；unpack() 同时返回已消费字节数。
    encoded = request.to_bytes()
    decoded, consumed = Header().unpack(encoded)

    # 边界层必须检查 consumed，避免把截断或尾随数据误当成一条合法消息。
    assert consumed == len(encoded)
    assert decoded.address.value == 0x1234
    assert decoded.opcode.value is Opcode.WRITE
    assert decoded.payload.value == [0xDE, 0xAD, 0xBE, 0xEF]
    print(f"已编码 {len(encoded)} 字节：{encoded.hex()}")
    print(f"解码结果：opcode={decoded.opcode.value.name}，address=0x{decoded.address.value:04x}")


if __name__ == "__main__":
    main()
