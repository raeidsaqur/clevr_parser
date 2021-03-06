#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# File: visualizer.py
# Author:
# Email:
# Created on: 2020-05-02
#
# This file is part of CLEVR-PARSER
# Distributed under terms of the MIT License
# https://github.com/raeidsaqur/clevr-parser

import logging

from .backend import VisualizerBackend
from ..visualizer import Visualizer

logger = logging.getLogger(__name__)
import copy

try:
    import matplotlib
    import matplotlib.pyplot as plt
    #import pygraphviz as pgv
    import networkx as nx
except ImportError as ie:
    logger.error(f"Install NetworkX: {ie.name}")

__all__ = ['MatplotlibVisualizer']

@Visualizer.register_backend
class MatplotlibVisualizer(VisualizerBackend):
    """
    Visualizer for CLEVR Graphs
    """
    __identifier__ = 'matplotlib'

    def __init__(self):
        super().__init__()

    @classmethod
    def draw_graph(cls, G: nx.Graph, *args, **kwargs):
        """
        :param G: The input Graph of type nx.MultiGraph (undirectional)
                  or nx.MultiDiGraph (Directional)
        :param args: Additional args
        :param kwargs: Additoinal kw arguments like pos (for image_scene_graphs), ax_title etc.
        :return:
        """
        return cls.draw_graph_matplotlib(G, *args, **kwargs)

    @classmethod
    def draw_graph_matplotlib(cls, G,  en_graphs=None, doc=None,
                           hnode_sz=2000, anode_sz=2000,
                           hnode_col='tab:blue', anode_col='tab:red',
                           font_size=14, attr_font_size=10,
                           figsize=(11, 9),
                           dpi = 100,
                           show_edge_labels=True,
                           show_edge_attributes=False,
                           layout='graphviz',
                           plot_box=False,
                           save_file_path=None,
                           ax_title=None):
        ### Nodes
        NDV = G.nodes(data=True)
        NV = G.nodes(data=False)
        _is_head_node = lambda x: 'obj' in x
        _is_attr_node = lambda x: 'obj' not in x
        head_nodes = list(filter(_is_head_node, NV))
        attr_nodes = list(filter(_is_attr_node, NV))
        assert len(NDV) == len(head_nodes) + len(attr_nodes)

        assert layout in ['graphviz', 'circular'], "Only 'graphviz' and 'circular' are supported"
        if layout == 'graphviz':
            from networkx.drawing.nx_agraph import graphviz_layout
            pos = graphviz_layout(G, prog='neato')

            pos_shadow = copy.deepcopy(pos)
            shift_amount = 0.001
            for k, v in pos_shadow.items():
                x = v[0] + shift_amount
                y = v[1] - shift_amount
                pos_shadow[k] = (x, y)
        elif layout == 'circular':
            pos = cls.get_positions(G, head_nodes, attr_nodes)

            # Create position copies for shadows, and shift shadows
            # See: https://gist.github.com/jg-you/144a35013acba010054a2cc4a93b07c7
            pos_shadow = copy.deepcopy(pos)
            shift_amount = 0.001
            for idx in pos_shadow:
                pos_shadow[idx][0] += shift_amount
                pos_shadow[idx][1] -= shift_amount

        nsz = [hnode_sz if 'obj' in node else anode_sz for node in G.nodes]
        # nsz2 = list(map(lambda node: hnode_sz if 'obj' in node else anode_sz, G.nodes))
        nc = [hnode_col if 'obj' in node else anode_col for node in G.nodes]
        #### Node Labels: Label head nodes as obj or obj{i}, and attr nodes with their values:
        _label = lambda node: node[1]['val'] if 'obj' not in node[0] else node[0]
        _labels = list(map(_label, G.nodes(data=True)))
        labels = dict(zip(list(G.nodes), _labels))

        ### Edges
        #### Edge Labels
        edge_labels = {}
        if show_edge_attributes:
            for u, v, d in G.edges(data=True):
                edge_labels.update({(u, v): d})
        else:
            for u, v, d in G.edges(data=True):
                if next(iter(d)) in ['matching_re', 'spatial_re']:
                    edge_labels.update({(u, v): d[next(iter(d))]})
                else:
                    edge_labels.update({(u, v): next(iter(d))})

                    # for k, v in en_graphs.items():
        #     edge_labels.update(v['edge_labels'])

        edgelist = G.edges(data=True)

        ## Draw ##

        # Render (MatPlotlib)
        plt.axis('on' if plot_box == True else "off")
        # fig, axs = plt.subplots(1, 2)
        # axs[1].set_title(f"{doc}")
        fig, ax = plt.subplots(1, 1, figsize=figsize, dpi=dpi)
        ax.set_title(ax_title, wrap=True)

        nx.draw_networkx_nodes(G, pos, node_size=nsz, node_color=nc)
        nx.draw_networkx_nodes(G, pos_shadow, node_size=nsz, node_color='k', alpha=0.2)

        nx.draw_networkx_edges(G, pos, edgelist=edgelist)

        # Draw node labels (different font sizes for head and attribute nodes)
        pos_head, pos_attr = {k: v for k, v in pos.items() if k in head_nodes}, \
                             {k: v for k, v in pos.items() if k in attr_nodes}
        labels_head, labels_attr = {k: v for k, v in labels.items() if k in head_nodes}, \
                                   {k: v for k, v in labels.items() if k in attr_nodes}
        nx.draw_networkx_labels(G, pos_head, labels=labels_head, font_size=font_size, font_color='k',
                                font_family='sans-serif')
        nx.draw_networkx_labels(G, pos_attr, labels=labels_attr, font_size=attr_font_size, font_color='k',
                                font_family='sans-serif')

        # Draw edge labels
        if show_edge_labels:
            nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, label_pos=0.5, font_size=8)

        if save_file_path is not None:
            plt.savefig(save_file_path, dpi=150)
        # if pygraphviz_enabled:
        #   nx.write_dot(G, 'file.dot')
        plt.show()

        return G

    @classmethod
    def get_positions(cls, G, head_nodes, attr_nodes):
        '''
        Arranges only the head nodes in a circular layout, and
        attribute nodes in a random layout. Then creates a spring
        layout on top of that

        Arguments:
            G: the networkx graph
            head_nodes: head_nodes of the graph
            attr_nodes: attribute nodes of the graph

        Returns:
            The positions to be fed to spring layout
        '''
        # Generate the subgraph containing only the head nodes
        head_subgraph = G.subgraph(head_nodes)

        # Generate layouts for the head nodes and attribute nodes
        head_pos = nx.circular_layout(head_subgraph, scale=1.5)
        random_pos = nx.random_layout(G)

        # Assign polar coordinates in a sequential fashion
        for node, sub_node in zip(head_nodes, head_subgraph.nodes):
            random_pos[node] = head_pos[sub_node]

        # Create a spring layout
        pos = nx.spring_layout(G, k=1, pos=random_pos, fixed=head_nodes)

        return pos        
