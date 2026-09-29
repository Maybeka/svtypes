"""Cookbook 第 6 章：发布可重复生成的 SV、C++、schema 和 coverage 产物。"""

from pathlib import Path
from tempfile import TemporaryDirectory

from svtypes import Bit, Package, SvObject, generate, svobj


def main() -> None:
    # Package 是生成边界：只注册需要一同发布、相互引用的类型。
    package = Package("cookbook_generated")

    @svobj(registry=package)
    class GeneratedPacket(SvObject):
        # @svobj 将 class 注册到 package；不注册就不会出现在 package 产物中。
        opcode = Bit[2]()
        address = Bit[16]()

    # 教程用临时目录，避免污染工作树；工程中请替换为受管理的输出目录。
    with TemporaryDirectory(prefix="svtypes-cookbook-") as directory:
        output = Path(directory)
        first = generate(package, output)
        # check 不重写文件，只验证生成物没有因声明变化而漂移，适合 CI。
        check = generate(package, output, mode="check")
        assert first.written
        assert not first.failed
        assert check.is_current
        print("已生成：", ", ".join(first.written))
        print("check 模式：所有生成物均为最新")


if __name__ == "__main__":
    main()
