from svtypes import SvObject, SvStruct, Queue, Int, Bits, svobj

@svobj
class MyPacket(SvStruct):
    id = Int()
    data = Bits(16)

@svobj
class PacketManager(SvObject):
    queue = Queue(MyPacket())

def test_recursive_serialization():
    pm = PacketManager()

    # 1. Prepare data
    p1 = MyPacket()
    p1.id.value = 1
    p1.data.value = 0xAAAA

    p2 = MyPacket()
    p2.id.value = 2
    p2.data.value = 0xBBBB

    # 2. Add to queue
    pm.queue.push_back(p1)
    pm.queue.push_back(p2)

    # 3. Pack
    b = pm.to_bytes()
    assert b[0] == 1
    assert b[1:5] == b"SVXO"
    assert b"PacketManager" in b
    assert b.count(b"SVXO") == 1

    # 4. Unpack
    pm2 = PacketManager()
    pm2.from_bytes(b)

    assert len(pm2.queue.value) == 2
    assert pm2.queue.value[0].id.value == 1
    assert pm2.queue.value[0].data.value == 0xAAAA
    assert pm2.queue.value[1].id.value == 2
    assert pm2.queue.value[1].data.value == 0xBBBB

    print("Recursive serialization test passed!")

if __name__ == "__main__":
    from svtypes import SvObject # For PacketManager base class
    test_recursive_serialization()
