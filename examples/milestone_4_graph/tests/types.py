from svtypes import Int, Object, Queue, SvObject, get_package, svobj


graph_pkg = get_package("milestone_4_graph")


@svobj(registry=graph_pkg)
class GraphNode(SvObject):
    data = Int()
    next = Object("GraphNode", registry=graph_pkg)


@svobj(registry=graph_pkg)
class GraphPair(SvObject):
    tag = Int()
    left = Object("GraphNode", registry=graph_pkg)
    right = Object("GraphNode", registry=graph_pkg)


@svobj(registry=graph_pkg)
class GraphQueue(SvObject):
    tag = Int()
    nodes = Queue(GraphNode())


@svobj(registry=graph_pkg)
class GraphRefQueue(SvObject):
    tag = Int()
    nodes = Queue(Object("GraphNode", registry=graph_pkg))
