"""Cookbook 第 2 章：使用动态容器，并保留对象图中的共享身份。"""

from svtypes import Bit, DynArray, Object, Queue, SvObject, get_package, svobj


# Package/registry 是对象 handle 类型归属的边界；示例可重复运行，所以先清空它。
MODEL = get_package("cookbook_graph")
MODEL.clear()


@svobj(registry=MODEL)
class Node(SvObject):
    code = Bit[8]()
    # Object[...] 是 handle：编码时保留身份，而不是把子对象当作纯嵌套值复制。
    next = Object["Node"](registry=MODEL)


@svobj(registry=MODEL)
class Message(SvObject):
    # DynArray 是长度可变的值序列；max_length 是声明期资源边界。
    bytes = DynArray[Bit[8]](max_length=32)
    # Queue 中保存 handle，故同一个节点可以被引用多次。
    path = Queue[Object["Node"](registry=MODEL)]()


def main() -> None:
    # 构造两个节点和一个真正的环，模拟描述符/链表等常见对象图。
    first, second = Node(), Node()
    first.code.value, second.code.value = 10, 20
    first.next = second
    second.next = first  # a real cycle, not a copied nested value

    message = Message()
    message.bytes.value = [1, 2, 3]
    message.path.value = [first, second, first]  # 第一个节点故意出现两次。
    decoded, consumed = Message().unpack(message.to_bytes())

    assert consumed == len(message.to_bytes())
    assert decoded.bytes.value == [1, 2, 3]
    # “is” 验证共享身份；只比较 value 无法发现图被错误复制。
    assert decoded.path[0] is decoded.path[2]
    assert decoded.path[0].next is decoded.path[1]
    assert decoded.path[1].next is decoded.path[0]
    print("解码后的对象图同时保留了环和重复引用")


if __name__ == "__main__":
    main()
