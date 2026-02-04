from typing import List

import networkx as nx


class TNode:
    def __init__(self, node_id, attributes):
        self.node_id = node_id
        self.attributes = attributes

    def __str__(self):
        attr_str = ', '.join([f"{key}={value}" for key, value in self.attributes.items()])
        return f"{self.node_id}({attr_str})"


class TEdge:
    def __init__(self, source, target, attributes):
        self.source = source
        self.target = target
        self.attributes = attributes

    def __str__(self):
        attr_str = ', '.join([f"{key}={value}" for key, value in self.attributes.items()])
        return f"({self.source} -> {self.target}): [{attr_str}])"


class TGraph:
    def __init__(self, graph_type, graph_name, t_node_list: List[TNode], t_edge_list: List[TEdge]):
        self.graph_type = graph_type
        self.graph_name = graph_name
        self.node_list = t_node_list  # List of TNode objects
        self.edge_list = t_edge_list  # List of TEdge objects

    def __str__(self):
        node_str = ",\n    ".join(str(node) for node in self.node_list)
        edge_str = ",\n    ".join(str(edge) for edge in self.edge_list)
        return f"""
{self.graph_type}[{self.graph_name}] {{
    node_list: [
        {node_str}
    ],
    edge_list: [
        {edge_str}
    ]
}}
        """.strip()


def convert_to_func_call_graph(graph: nx.MultiDiGraph, graph_type="Graph", graph_name="DApp_Fault_Function_Call_Graph"):
    t_node_list = []
    t_edge_list = []

    # Extract nodes
    for node in graph.nodes:
        attributes = {
            # "type": graph.nodes[node].get("type", ""),
            "code": graph.nodes[node].get("code", ""),
            # "is_fault": graph.nodes[node].get("is_fault", False),
            "leakage score": graph.nodes[node].get("leakage", 0.0)
        }
        t_node = TNode(node, attributes)
        node_name = format_node_name(node)
        t_node_list.append(t_node)

    # Extract edges
    for u, v, key, data in graph.edges(data=True, keys=True):
        edge_attrs = {
            "type": data.get("type", ""),
            "tx_index": data.get("tx_index", -1),
            "call_index": data.get("call_index", -1),
            "value": data.get("value", 0),
            "gas": data.get("gas", 0),
            "gas_used": data.get("gas_used", 0)
        }
        t_edge = TEdge(u, v, edge_attrs)
        t_edge_list.append(t_edge)

    return TGraph(graph_type, graph_name, t_node_list, t_edge_list)


def convert_to_graph(graph: nx.MultiDiGraph, graph_type="Graph", graph_name=""):
    t_node_list = []
    t_edge_list = []

    # Extract nodes
    for node in graph.nodes:
        attributes = graph.nodes[node]
        t_node = TNode(node, attributes)
        t_node_list.append(t_node)

    # Extract edges
    for u, v, key, data in graph.edges(data=True, keys=True):
        edge_attrs = data
        t_edge = TEdge(u, v, edge_attrs)
        t_edge_list.append(t_edge)

    return TGraph(graph_type, graph_name, t_node_list, t_edge_list)


def format_node_name(node):
    node_len = str(node).split("#")
    if len(node_len) == 3:
        node_name = node_len[1] + "#" + node_len[2]
    else:
        node_name = node_len[-1]
    return node_name
