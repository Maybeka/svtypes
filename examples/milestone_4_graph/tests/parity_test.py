import svx

from .types import GraphNode, GraphPair, GraphQueue, GraphRefQueue


def _node(data: int) -> GraphNode:
    node = GraphNode()
    node.data.value = data
    node.next = None
    return node


@svx.export
def python_puts_shared_pair():
    child = _node(101)
    pair = GraphPair()
    pair.tag.value = 100
    pair.left = child
    pair.right = child

    svx.channel("m4.shared.py_to_sv").put(pair)
    svx.display(f"python_puts_shared_pair size={len(pair.to_bytes())}")


@svx.export
def python_puts_self_cycle():
    node = _node(201)
    node.next = node

    svx.channel("m4.self.py_to_sv").put(node)
    svx.display(f"python_puts_self_cycle size={len(node.to_bytes())}")


@svx.export
def python_puts_queue_repeat():
    node = _node(501)
    queue = GraphQueue()
    queue.tag.value = 500
    queue.nodes.value = [node, node]

    svx.channel("m4.queue.py_to_sv").put(queue)
    svx.display(f"python_puts_queue_repeat size={len(queue.to_bytes())}")


@svx.export
def python_puts_ref_queue_repeat():
    node = _node(701)
    queue = GraphRefQueue()
    queue.tag.value = 700
    queue.nodes.value = [node, node]

    svx.channel("m4.ref_queue.py_to_sv").put(queue)
    svx.display(f"python_puts_ref_queue_repeat size={len(queue.to_bytes())}")


@svx.export
def python_gets_shared_pair():
    pair = svx.channel("m4.shared.sv_to_py").get(GraphPair)
    svx.display(f"python_gets_shared_pair tag={pair.tag.value}")

    if pair.tag.value != 300:
        raise AssertionError(f"unexpected tag: {pair.tag.value}")
    if pair.left is not pair.right:
        raise AssertionError("shared child identity was not preserved")
    if pair.left.data.value != 301:
        raise AssertionError(f"unexpected shared child data: {pair.left.data.value}")


@svx.export
def python_gets_self_cycle():
    node = svx.channel("m4.self.sv_to_py").get(GraphNode)
    svx.display(f"python_gets_self_cycle data={node.data.value}")

    if node.data.value != 401:
        raise AssertionError(f"unexpected node data: {node.data.value}")
    if node.next is not node:
        raise AssertionError("self-cycle identity was not preserved")


@svx.export
def python_gets_queue_repeat():
    queue = svx.channel("m4.queue.sv_to_py").get(GraphQueue)
    svx.display(f"python_gets_queue_repeat tag={queue.tag.value}")

    if queue.tag.value != 600:
        raise AssertionError(f"unexpected queue tag: {queue.tag.value}")
    if len(queue.nodes.value) != 2:
        raise AssertionError(f"unexpected queue size: {len(queue.nodes.value)}")
    if queue.nodes[0] is not queue.nodes[1]:
        raise AssertionError("queue repeated reference identity was not preserved")
    if queue.nodes[0].data.value != 601:
        raise AssertionError(f"unexpected queue node data: {queue.nodes[0].data.value}")


@svx.export
def python_gets_ref_queue_repeat():
    queue = svx.channel("m4.ref_queue.sv_to_py").get(GraphRefQueue)
    svx.display(f"python_gets_ref_queue_repeat tag={queue.tag.value}")

    if queue.tag.value != 800:
        raise AssertionError(f"unexpected ref queue tag: {queue.tag.value}")
    if len(queue.nodes.value) != 2:
        raise AssertionError(f"unexpected ref queue size: {len(queue.nodes.value)}")
    if queue.nodes[0] is not queue.nodes[1]:
        raise AssertionError("ref queue repeated reference identity was not preserved")
    if queue.nodes[0].data.value != 801:
        raise AssertionError(f"unexpected ref queue node data: {queue.nodes[0].data.value}")
