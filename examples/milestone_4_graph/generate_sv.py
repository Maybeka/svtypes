from examples.milestone_4_graph.tests.types import GraphNode, GraphPair, GraphQueue, GraphRefQueue


def main() -> None:
    print("// Generated from examples.milestone_4_graph.tests.types")
    print("// Do not edit by hand.")
    print()
    print("typedef class GraphNode;")
    print("typedef class GraphPair;")
    print("typedef class GraphQueue;")
    print("typedef class GraphRefQueue;")
    print()
    print(GraphNode.to_sv_obj())
    print()
    print(GraphPair.to_sv_obj())
    print()
    print(GraphQueue.to_sv_obj())
    print()
    print(GraphRefQueue.to_sv_obj())


if __name__ == "__main__":
    main()
