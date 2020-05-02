#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# File   : spacy_parser.py
# Author : Raeid Saqur
# Email  : raeidsaqur@gmail.com
# Date   : 09/21/2019
#
# This file is part of PGFM Parser.
# Distributed under terms of the MIT license.
# https://github.com/raeidsaqur/clevr-parser

from .. import database
from ..parser import Parser
from .backend import ParserBackend
from .custom_components_clevr import CLEVRObjectRecognizer
from .spatial_recognizer import SpatialRecognizer
from .matching_recognizer import MatchingRecognizer
from ..utils import *

__all__ = ['SpacyParser']

from functools import reduce
from operator import itemgetter
from typing import List, Dict, Tuple, Sequence
import copy
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logging.getLogger('matplotlib').setLevel(logging.ERROR)
logger = logging.getLogger(__name__)
import os

try:
    import matplotlib
    import matplotlib.pyplot as plt
    import pygraphviz as pgv
    import networkx as nx
except ImportError as ie:
    logger.error(f"Install NetworkX: {ie.name}")

import numpy as np
np.random.seed(42)
import scipy.sparse as sp


@Parser.register_backend
class SpacyParser(ParserBackend):
    """
    Scene graph parser based on spaCy.
    """

    __identifier__ = 'spacy'

    def __init__(self, model='en', **kwargs):
        """
        Pass `has_spatial=True` to invoke spatial relation parsing
        Args:
            model (str): a spec for the spaCy model. (default: en). Please refer to the
            official website of spaCy for a complete list of the available models.
            This option is useful if you are dealing with languages other than English.
        """
        super().__init__()
        self.__model = model
        try:
            import spacy
        except ImportError as e:
            raise ImportError('Spacy backend requires the spaCy library. Install spaCy via pip first.') from e
        try:
            self.__nlp = spacy.load(model)
        except OSError as e:
            raise ImportError('Unable to load the English model. Run `python -m spacy download en` first.') from e

        self.__entity_recognizer = CLEVRObjectRecognizer(self.__nlp)
        self.__spatial_recognizer = None
        self.has_spatial = kwargs.get('has_spatial')  # Spatial Recog Flag
        if self.has_spatial:
            # N.b. Any calculations based on presumption that doc.ents only has objs, need to
            # filtered first with this on. For e.g. layout with len(doc.ents)
            logger.debug(f"Activated spatial recognizer")
            self.__spatial_recognizer = SpatialRecognizer(self.__nlp)
        self.has_matching = kwargs.get('has_matching')  # Spatial Recog Flag
        if self.has_matching:
            logger.debug(f"Activated matching recognizer")
            self.__matching_recognizer = MatchingRecognizer(self.__nlp)

    @property
    def entity_recognizer(self):
        return self.__entity_recognizer

    @entity_recognizer.setter
    def entity_recognizer(self, er):
        self.__entity_recognizer = er

    @property
    def spatial_recognizer(self):
        return self.__spatial_recognizer

    @spatial_recognizer.setter
    def spatial_recognizer(self, sr):
        self.__spatial_recognizer = sr

    @property
    def matching_recognizer(self):
        return self.__matching_recognizer

    @matching_recognizer.setter
    def matching_recognizer(self, mr):
        self.__matching_recognizer = mr

    @property
    def nlp(self):
        return self.__nlp

    @nlp.setter
    def nlp(self, nlp):
        self.__nlp = nlp

    @property
    def model(self):
        return self.__model

    @model.setter
    def model(self, model):
        self.__model = model

    def parse(self, sentence: str, index=0, filename=None, return_doc=True, skip_plurals=False, **kwargs):
        """
            The spaCy-based parser parse the sentence into scene graphs based on the dependency parsing
            of the sentence by spaCy.

            Returns a nx.MultiGraph and Spacy.doc
        """
        doc = self.nlp(sentence)
        if skip_plurals:
            is_plural = doc._.has_shapes
            if is_plural:
                logger.info(f'{sentence} contains plural, skipping all CLEVR_OBJS as an edge case')
                return None, f"SKIP_img {index}_{filename}"

        graph, en_graphs = self.get_nx_graph_from_doc(doc, **kwargs)

        if self.has_spatial:
            spatial_res = self.filter_spatial_re(doc.ents)
            if spatial_res and len(spatial_res) > 0:
                self.update_graph_with_spatial_re(graph, doc)
        if self.has_matching:
            matching_res = self.filter_matching_re(doc.ents)
            if matching_res and len(matching_res) > 0:
                self.update_graph_with_matching_re(graph, doc)

        # N.b. The ordering of doc.ents and graph.nodes should be aligned
        if return_doc:
            return graph, doc
        return graph

    def parse_Gs(self, sentence: str, index=0, filename=None, return_doc=True):
        """
        This is intended to be invoked by the text side (not grounding image pipeline)
        Returns a nx.graph and doc
        """

        doc = self.__nlp(sentence)
        graph, en_graphs = self.get_nx_graph_from_doc(doc)

        ## MODIFY HERE ##

        if return_doc:
            return graph, doc
        return graph

    def get_clevr_text_vector_embedding(self, text, ent_vec_size=384, embedding_type=None):
        """
        N.b. This doesn't return uniform embedding dim across different graphs,
        which can create training problems.

        Takes a text input and returns the feature vector X
        :param text: Caption or Question (any NL input)
        :param ent_vec_size: size of each entity.
        :param embedding_type: GloVe, BERT, GPT etc.
        :return:
        """
        assert text is not None
        Gs, doc = self.parse(text, return_doc=True)
        if Gs is None and 'SKIP' in doc:
            logger.info(f'{text} contains plural (i.e. label CLEVR_OBJS')
            return None, f"SKIP_{text}"

        doc_emd = self.get_clevr_doc_vector_embedding(doc, ent_vec_size=ent_vec_size, embedding_type=embedding_type)

        return Gs, doc_emd

    def get_clevr_doc_vector_embedding(self, doc,
                                       attr_vec_size=96,
                                       ent_vec_size=384,
                                       include_obj_node_emd=True,
                                       embedding_type=None):
        """
        Embedding = [<Gs-obj>, <Z>, <C>, <M>, <S>]
        To keep dim d constant, we pad missing attrs with [0]*attr_vec_size
        <Gs-obj> is just a copy of [<Z>, <C>, <M>, <S>].
        So total ent_vec_size = (attr_vec_size*4) * 2

        :param doc: A spacy Doc
        :param ent_vec_size: embedding vector size of each clevr entity
        :param include_obj_node_emd: if True, a wrapper obj node "obj" is prepended to the entity vector embedding
        :param embedding_type: one-hot, bag_of_words, GloVe etc.
        :return: vector embedding for all the clevr entitites in a doc
        """
        assert doc is not None
        entities = self.filter_clevr_objs(doc.ents)
        embed_sz = len(entities) * ent_vec_size
        if include_obj_node_emd:
            embed_sz *= 2  # 2x size due to tiling of obj node vector
        doc_vector = np.zeros((embed_sz,), dtype=np.float32).reshape((1, -1))
        ent_vecs = []
        for entity in entities:
            # if entity.label_ != 'CLEVR_OBJ':
            if entity.label_ not in ('CLEVR_OBJS', 'CLEVR_OBJ'):
                continue
            ent_vec = self.get_clevr_entity_vector_embedding(entity, ent_vec_size, include_obj_node_emd, embedding_type)
            ent_vecs.append(ent_vec)

        doc_vector = np.hstack(tuple(ent_vecs)).reshape((1, -1))

        assert doc_vector.shape[1] == embed_sz
        return doc_vector

    def get_clevr_entity_matrix_embedding(self, entity, dim=96, include_obj_node_emd=True, embedding_type=None):
        """
        Atomic function for generating matrix embedding from a doc entity:

        :param entity: A spacy Doc.entity
        :param dim: the dim of the embd matrix, default = 96, the same dim as each attr node
        :param include_obj_node_emd: if True, a wrapper obj node "obj" is prepended to the entity vector embedding
        :param embedding_type: one-hot, bag_of_words, GloVe etc.
        :return: an N by M embedding matrix, where M = dim and N is the num of nodes of a generated graph of the entity
        """
        label = entity.label_
        # if label is None or label != "CLEVR_OBJ":
        if (label is None) or (label not in ("CLEVR_OBJS", "CLEVR_OBJ")):
            raise TypeError("The entity must be a CLEVR_OBJ(S) entity")

        embds_poss = []
        for token in entity:
            _v, pos = self.get_attr_token_vector_embedding(token, size=dim, embedding_type=embedding_type)
            embds_poss.append((_v, pos))
        # embds = reduce(lambda a,b: np.vstack((a,b)), embds) if len(embds) > 1 else embds[0]
        embds_poss.sort(key=itemgetter(1))
        embds = list(map(lambda x: x[0], embds_poss))
        embds = np.array(embds,
                         dtype=np.float32).squeeze()  # Bug: when len(entity) == 1 (e.g. 'thing') -> deforms shape
        if len(embds.shape) == 1 and embds.shape[0] == dim:
            # when entity is defined by a single token. e.g. that 'thing'
            embds = embds.reshape(1, -1)
        obj_embd = np.mean(embds, axis=0)
        embds = np.vstack((obj_embd, embds))

        assert embds.shape[-1] == dim

        return embds

    def get_clevr_entity_vector_embedding(self, entity, size=384, include_obj_node_emd=True, embedding_type=None):
        """
        Atomic function for generating vector embedding from a doc entity:

        :param entity: A spacy Doc.entity
        :param size: N.b., the token embedding size will be ent_size / 4 (rank-4 is the full tensor)
        :param include_obj_node_emd: if True, a wrapper obj node "obj" is prepended to the entity vector embedding
        :param embedding_type: one-hot, bag_of_words, GloVe etc.
        :return: a uniform sized entity vector embedding, if it's not a full-rank attr tensor,
        then the entity vector needs to be padded. For e.g., "red thing" -> "<C> <S>" with missing
        <Z>, <M> attrs, in which case, <Z> <C> <M> <S> entity embedding will have <Z> <M> padded
        """
        token_sz = int(size / 4)
        label = entity.label_
        if label is None or label != "CLEVR_OBJ":
            raise TypeError("The entity must be a CLEVR_OBJ entity")

        # missing attr tokens are represented with token_sz * 0.0
        ent_vector = np.zeros((size,), dtype=np.float32).reshape((1, -1))
        for token in entity:
            _v, pos = self.get_attr_token_vector_embedding(token, size=token_sz, embedding_type=embedding_type)
            s_idx = pos * token_sz
            e_idx = s_idx + token_sz
            ent_vector[:, s_idx: e_idx] = _v

        if include_obj_node_emd:
            # Duplicate and prepend the env_vector
            ent_vector = np.tile(ent_vector, 2)

        return ent_vector

    def _get_attr_token_pos(self, token):
        """
        :param token: <Z> <C> <M> <S>
        :return: a pos int in the range (0, 3) based on the relative ordering
        """
        t = token  # N.b. the pipeline must have clevr 'ent_recognizer' added with extensions
        pos = 0
        if t._.is_size:
            pos = 0
        elif t._.is_color:
            pos = 1
        elif t._.is_material:
            pos = 2
        elif t._.is_shape:
            pos = 3
        else:
            # fon anything else, place it after the attr pos
            pos = 4

        return int(pos)

    def get_attr_token_vector_embedding(self, token, size=96, embedding_type=None):
        """

        :param token: A Spacy token belonging to a (Doc) entity and awith clevr entity recognizer
        baked in, i.e., the clevr extensions are (presumed) available
        :param size: The dimension of the embedding vector
        :param embedding_type:
        :return: returns a token embedding vector of specified size (default=96) and relative position
        in an entity [Z C M S] embedding vector
        """
        # default_token_vector_dim = 96
        # size = size if size is not None else default_token_vector_dim
        if embedding_type is None:
            # Use the default embedding type
            vector = token.vector.reshape(1, -1)
        pos = self._get_attr_token_pos(token)

        return vector, pos

    @staticmethod
    def get_attr_node_from_token(token, ent_num=0):
        """
        :param token: A Spacy token belonging to a (Doc) entity
        :param ent_num: the entity number, default 0, used to assign markers
        like '<Z>' (default), or <Z2> (for 2obj)
        :return: a nx graph node construction structure
        """
        assert token is not None
        node_keys = ('label', 'val')
        # Reformat in nx.graph node construct structure
        _n_fn = lambda s, a, t: tuple((s, dict(zip(node_keys, (a, t.text)))))
        t = token  # N.b. the pipeline must have clevr 'ent_recognizer' added with extensions
        if t._.is_size:
            s = "<Z>" if ent_num <= 1 else f"<Z{ent_num}>"
            node = tuple(_n_fn(s, 'size', t))
        elif t._.is_color:
            s = "<C>" if ent_num <= 1 else f"<C{ent_num}>"
            node = _n_fn(s, 'color', t)
        elif t._.is_material:
            s = "<M>" if ent_num <= 1 else f"<M{ent_num}>"
            node = _n_fn(s, 'material', t)
            # node = ('M', dict(zip(node_keys, ('material', t.text))))
        elif t._.is_shape:
            s = "<S>" if ent_num <= 1 else f"<S{ent_num}>"
            node = _n_fn(s, 'shape', t)
        elif t._.is_shapes:
            # Handle CLEVR_OBJS, plural shapes
            # This is sound. the head_node label 'CLEVR_OBJS' captures
            # plurality. All attribute values are the same
            s = "<S>" if ent_num <= 1 else f"<S{ent_num}>"
            node = _n_fn(s, 'shape', t)
        else:
            # an unknown node: set attr_id, label as <UNK{ent_num}>
            s = "<UNK>" if ent_num <= 1 else f"<S{ent_num}"
            node = _n_fn(s, '<UNK>', t)

        return node

    def get_pos_from_img_scene(self, scene, *args, **kwargs):
        scene_img_idx = scene["image_index"]
        scene_img_fn = scene["image_filename"]
        clevr_objs = scene['objects']
        nco = len(clevr_objs)
        if kwargs.get('cap_to_10_objs'):
            assert nco <= 10

        p = lambda o: tuple(o['position'])  # (x, y, z) co-ordinates
        pos = list(map(p, clevr_objs))

        return pos

    def get_caption_from_img_scene(self, scene, *args, **kwargs):
        """
        The imgage scene here is the parsed img scene, not the oracle scene graph.
        Only other info available are the image 'pos' as parsed by the scene
        derenderer.
        # RS TODO: could parse pos here @see `get_pos_from_img_scene`
        :param scene:
        :param args:
        :param kwargs:
        :return:
        """
        scene_img_idx = scene["image_index"]
        scene_img_fn = scene["image_filename"]
        clevr_objs = scene['objects']
        nco = len(clevr_objs)
        if kwargs.get('cap_to_10_objs'):
            assert nco <= 10
        if nco == 0:
            logger.warning(f"Scene derendering appears to have failed on {scene_img_idx}: {scene_img_fn}"
                           f"\nThe derenderer failed to produce any proposal for this scene image."
                           f"\nSkipping this scene image from data")
            # return f"SKIP_{scene_img_idx}_{scene_img_fn}"
            return None

        f = lambda o: " ".join([o['size'], o['color'], o['material'], o['shape']])
        concat = lambda x, y: x + ", " + y
        caption = reduce(concat, map(f, clevr_objs))  # skeletal scene caption without pos, rel
        # p = lambda o: tuple(o['position'])  # (x, y, z) co-ordinates
        # pos = list(map(p, clevr_objs))

        return caption

    def get_doc_from_img_scene(self, scene, *args, **kwargs):
        """
        TODO: not utilizing the position info in parsed img scene
        :param scene:
        :param args:
        :param kwargs:
        :return:
        """
        scene_img_idx = scene["image_index"]
        scene_img_fn = scene["image_filename"]
        pos = self.get_pos_from_img_scene(scene, *args, *kwargs)
        caption = self.get_caption_from_img_scene(scene, *args, **kwargs)
        if caption is None:
            return None, f"SKIP_{scene_img_idx}_{scene_img_fn}"

        # graph, doc = self.parse(caption, return_doc=True)
        graph, doc = self.parse(caption,
                                    index=scene_img_idx,
                                   filename=scene_img_fn,
                                   return_doc=True,
                                   pos=pos)
        return graph, doc


    @classmethod
    def get_graph_from_entity(cls, entity, ent_num=0,
                              is_directed_graph=False,
                              is_attr_name_node_label=False,
                              head_node_prefix=None,
                              hnode_sz=1200, anode_sz=700,
                              hnode_col='tab:blue', anode_col='tab:red',
                              is_return_list=False,
                              is_debug=False, **kwargs):
        """
        The atomic graph constructor.
        :param entity: atomic CLEVR Object
        :param ent_num: the id of the object in context of the full graph
        :param is_attr_name_node_label:
        :param head_node_prefix:
        :param hnode_sz:
        :param anode_sz:
        :param hnode_col:
        :param anode_col:
        :param is_return_list:
        :param is_debug:
        :return:
        """
        obj_vals = (entity.label_, entity.text)
        node_keys = ('label', 'val')
        pos = kwargs.get('pos')  # a tuple of (x,y,z) co-ordinates, valid for Gt only
        if pos is not None:
            obj_vals = (entity.label_, entity.text, pos)
            node_keys = ('label', 'val', 'pos')
        d = dict(zip(node_keys, obj_vals))
        head_node_id = "obj" if ent_num <= 1 else f"obj{ent_num}"
        if head_node_prefix and (head_node_prefix not in head_node_id):
            head_node_id = f"{head_node_prefix}-{head_node_id}"

        nodelist = [tuple((head_node_id, d))]
        # _z_fn = lambda a, t: dict(zip(node_keys, (a, t.text)))
        # _n_fn = lambda s, a, t: tuple( (s, _z_fn(a,t)) )
        _n_fn = lambda s, a, t: tuple((s, dict(zip(node_keys, (a, t.text)))))
        for t in entity:
            _node = cls.get_attr_node_from_token(t, ent_num)
            nodelist.append(_node)

        # Node Labels
        if is_attr_name_node_label:
            labels = dict(map(lambda x: (x[0], x[1]['label']), nodelist))
        else:
            # print(nodelist[0])
            labels = dict(map(lambda x: (x[0], x[1]['label']), [nodelist[0]]))
            a_labels = dict(map(lambda x: (x[0], x[1]['val']), nodelist[1:]))
            labels.update(a_labels)

        # Edge List & Labels:
        edgelist = []       # Redundant, this is EDV G.edges(data=True)
        edge_labels = {}  # edge_labels = {(u, v): d for u, v, d in G.edges(data=True)}
        _e_fn = lambda x: tuple((head_node_id, x[0], {x[1]['label']: x[1]['val']}))
        for i, node in enumerate(nodelist):
            if node[0] == head_node_id:
                continue
            edge = _e_fn(node)
            edgelist.append(edge)
            edge_label = f"{node[0]}:{node[1]['label']}"
            edge_labels.update({(head_node_id, node[0]): edge_label})

        G = nx.MultiDiGraph() if is_directed_graph else nx.MultiGraph()
        G.add_nodes_from(nodelist)
        G.add_edges_from(edgelist)

        l = len(nodelist) - 1
        nsz = [hnode_sz]
        nsz.extend([anode_sz] * l)
        nc = [hnode_col]
        nc.extend([anode_col] * l)

        if is_return_list:
            [G, nodelist, labels, edgelist, edge_labels, nsz, nc]
        return G, nodelist, labels, edgelist, edge_labels, nsz, nc

    def get_docs_from_nx_graph(cls, G: nx.Graph) -> List:
        nodes: nx.NodeDataView = G.nodes(data=True)
        # clevr_obj_nodes: List[Tuple] = list(filter(lambda n: n[1]['label'] == 'CLEVR_OBJ', nodes))
        clevr_spans: List[str] = list(map(lambda x: x[1]['val'], filter(lambda n: n[1]['label'] == 'CLEVR_OBJ', nodes)))
        # E.g. : ['small red rubber cylinder', 'large brown metal sphere']
        nco = len(clevr_spans)
        assert nco <= 10
        if nco == 0:
            logger.warning(f"No CLEVR_OBJ found in {clevr_spans}")
            return None
        _docs = []
        for cs in clevr_spans:
            _, _doc = cls.parse(cs)
            _docs.append(_doc)

        return _docs

    @classmethod
    def update_graph_with_matching_re(cls, G, doc, **kwargs):
        matching_res = cls.filter_matching_re(doc.ents)
        if matching_res is None:
            return G
        NV = G.nodes(data=False)
        _is_head_node = lambda x: 'obj' in x
        head_nodes = list(filter(_is_head_node, NV))
        objs = cls.filter_clevr_objs(doc.ents)
        relations = cls.extract_matching_relations(doc)
        assert len(objs) == len(head_nodes)
        assert len(relations) == len(matching_res)
        o2n_map = dict(zip(objs, head_nodes))
        sr2r_map = dict(zip(matching_res, relations))

        def add_nodes(n0, n1, r):
            if not G.has_edge(*(n0, n1, 'matching_re')):
                #G.add_edge(n0, n1, **{'label': 'matching_re', 'val':r})
                G.add_edge(n0, n1, matching_re=r)
                # G.add_edge(n0, n1,key=r)

        has_and = 'and' in doc.text
        # has_or = 'or' in doc.text
        # is_logical_re = has_and or has_or
        for i, ent in enumerate(doc.ents):
            if ent in objs:
                continue
            if ent in matching_res:
                _r = sr2r_map[ent]
                # Hack - Best effort: fail gracefully, don't crash
                try:
                    if i > 1:
                        if has_and:
                            n1 = o2n_map[doc.ents[i + 1]]
                            n0 = o2n_map[doc.ents[0]]
                        else:
                            n0 = o2n_map[doc.ents[i - 1]]
                            n1 = o2n_map[doc.ents[i + 1]]
                    else:
                        n0 = o2n_map[doc.ents[i - 1]]
                        n1 = o2n_map[doc.ents[i + 1]]
                    add_nodes(n0, n1, _r)
                except IndexError as ie:
                    print(ie)

        return G
        

    @classmethod
    def update_graph_with_spatial_re(cls, G: nx.MultiGraph, doc, **kwargs) -> nx.MultiGraph:
        spatial_res = cls.filter_spatial_re(doc.ents)
        if spatial_res is None:
            return G
        NV = G.nodes(data=False)
        _is_head_node = lambda x: 'obj' in x
        head_nodes = list(filter(_is_head_node, NV))
        objs = cls.filter_clevr_objs(doc.ents)
        relations = cls.extract_spatial_relations(doc)
        assert len(objs) == len(head_nodes)
        assert len(relations) == len(spatial_res)
        o2n_map = dict(zip(objs, head_nodes))
        sr2r_map = dict(zip(spatial_res, relations))

        def add_nodes(n0, n1, r):
            if not G.has_edge(*(n0, n1, 'spatial_re')):
                #G.add_edge(n0, n1, **{'label':'spatial_re', 'val': r})
                G.add_edge(n0, n1, spatial_re=r)
                # G.add_edge(n0, n1,key=r)

        has_and = 'and' in doc.text
        # has_or = 'or' in doc.text
        # is_logical_re = has_and or has_or
        for i, ent in enumerate(doc.ents):
            if ent in objs:
                continue
            if ent in spatial_res:
                _r = sr2r_map[ent]
                try:
                    if i > 1:
                        if has_and:
                            n1 = o2n_map[doc.ents[i + 1]]
                            n0 = o2n_map[doc.ents[0]]
                        else:
                            n0 = o2n_map[doc.ents[i - 1]]
                            n1 = o2n_map[doc.ents[i + 1]]
                    else:
                        n0 = o2n_map[doc.ents[i - 1]]
                        n1 = o2n_map[doc.ents[i + 1]]
                    add_nodes(n0, n1, _r)
                except IndexError as ie:
                    print(ie)
                except UnboundLocalError as lr:
                    print(lr)

        return G

    @classmethod
    def get_nx_graph_from_doc(cls, doc, head_node_prefix=None, **kwargs):
        """
        :param doc: doc obtained upon self.nlp(caption|text) contains doc.entities as clevr objs
        :return: a composed NX graph of all clevr objects along with pertinent info in en_graphs

        :param pos: passed in kwargs. contains List[Tuple(x,y,z)] of pos co-ordinates
        len(pos) == num_of clevr_objects
        """
        assert doc.ents is not None
        # objs = list(filter(lambda x: x.label_ in ['CLEVR_OBJ', 'CLEVR_OBJS'], doc.ents))
        objs = cls.filter_clevr_objs(doc.ents)
        nco = len(objs)
        if kwargs.get('cap_to_10_objs'):
            assert nco <= 10  # max number of clevr entities in one scene
        # Gs specific entities
        # parse will decorate with relations once G is returned
        # spatial_res = cls.filter_spatial_re(doc.ents)
        # matching_res = cls.filter_matching_re(doc.ents)
        # Gt specific components
        pos = kwargs.get('pos')
        if pos is not None:
            assert len(pos) == nco  # each clevr_obj and corresponding pos

        en_graph_keys = list(range(1, nco + 1))
        en_graph_vals = ['graph', 'nodelist', 'labels', 'edgelist', 'edge_labels', 'nsz', 'nc']
        en_graphs = dict.fromkeys(en_graph_keys)

        graphs = []  # list of all graphs corresponding to each entity
        for i, en in enumerate(objs):
            en_graph_key = en_graph_keys[i]
            # print(f"Processing graph {en_graph_key} ... ")
            pos_i = pos[i] if pos is not None else None
            _g = cls.get_graph_from_entity(en, head_node_prefix=head_node_prefix,
                                           ent_num=i + 1, is_return_list=True, pos=pos_i)
            if isinstance(_g[0], nx.Graph):
                graphs.append(_g[0])
            assert len(en_graph_vals) == len(_g)
            en_graph_dict = dict(zip(en_graph_vals, _g))
            en_graphs[en_graph_key] = en_graph_dict

        ## Multi-Obj case ##
        if len(graphs) > 0:
            G = nx.compose_all(graphs)
        else:
            raise ValueError("0 graphs could be parsed from the given spacy.Doc")

        return G, en_graphs

    def draw_clevr_img_scene_graph(self, scene,
                                   hnode_sz=1200, anode_sz=700,
                                   hnode_col='tab:blue', anode_col='tab:red',
                                   font_size=12,
                                   show_edge_labels=True,
                                   plot_box=False,
                                   save_file_path=None,
                                   debug=False):
        """
        Steps:
        1. generate (canonical) caption from the image scene for all the objects
        2. parser.parse(caption) -> graph, doc
        3. call parser.draw_clevr_obj_graph() # same used for text scene graph.

        Issues:
        1. Need to encode the positional information in image scene
        """
        graph, doc = self.get_doc_from_img_scene(scene)

        if graph is None and doc.contains("SKIP"):
            return None

        kwargs = {
            'hnode_sz': hnode_sz,
            'anode_sz': anode_sz,
            'hnode_col': hnode_col,
            'anode_col': anode_col,
            'font_size': font_size,
            'show_edge_labels': show_edge_labels,
            'plot_box': plot_box,
            'save_file_path': save_file_path,
            'debug': debug
        }
        G = self.__class__.draw_clevr_obj_graph(graph, doc, **kwargs)
        return G

    @classmethod
    def draw_clevr_obj_graph(cls, text_scene_graph, doc,
                             **kwargs):
        ax_title = f"{doc}"
        G, en_graphs = cls.get_nx_graph_from_doc(doc)
        G = cls.draw_graph(G, en_graphs, ax_title=ax_title, **kwargs)
        return G

    # ----------------------- Helpers  --------------------------------------- #
    @classmethod
    def _get_head_node_edges(cls, G: nx.Graph, head_node_id='obj') -> List:
        head_nodes = []
        for i, node in enumerate(list(G.nodes(data=False))):
            if 'obj' in node:
                head_nodes.append(node)
        print(f"head_nodes = {head_nodes}")
        return head_nodes

    @classmethod
    def _remove_head_node_edges(cls, G: nx.Graph, head_node_id='obj'):
        head_nodes = cls._get_head_node_edges(G, head_node_id)

        if len(head_nodes) > 1:
            # ToDo: Remove connection between head nodes
            for i, h_node in enumerate(head_nodes):
                if i == 0:
                    continue
                h = head_nodes[i - 1]
                t = h_node
                if G.has_edge(h, t):
                    G.remove_edges_from([(h, t)])

        return G

    @classmethod
    def _add_head_node_edges(cls, G: nx.Graph, head_node_id='obj'):
        """
        # ToDo: there needs to be connection among all head node permutations
        For now, just make the G connected component = 1
        Also, change edge key from '<R>, <R1>' to <rel> as connections should be
        order invariant

        """
        head_nodes = cls._get_head_node_edges(G, head_node_id)
        if len(head_nodes) > 1:
            # ToDo: there needs to be connection among all head node permutations
            for i, h_node in enumerate(head_nodes):
                if i == 0:
                    continue
                h = head_nodes[i - 1]
                t = h_node
                # TODO: Relations should be order invariant, remove i+1
                key = "<R>" if i <= 1 else f"<R{i + 1}>"
                G.add_edges_from([(h, t, {key: "tbd"})])
                # edge_labels.update({(h, t): key})

        return G

    # ----------------------- Helpers end --------------------------------------- #
    @classmethod
    def draw_graphviz(cls, G, pos=None, plot_box=False, ax_title=None):
        import random
        from networkx.drawing.nx_agraph import graphviz_layout

        NDV = G.nodes(data=True)
        NV = G.nodes(data=False)
        EV = G.edges(data=False)
        EDV = G.edges(data=True)

        is_head_node = lambda x: 'obj' in x
        is_snode = lambda x: 'Gs' in x
        is_tnode = lambda x: 'Gt' in x

        # Desiderata:
        # Draw the head_nodes a little larger, node_size=60 for hnodes, and 40 for anodes
        # Color the Gs, Gt nodes differently or shape (node_shape)

        # nsz = [60 if is_head_node(node) else 40 for node in NV]
        # ncol = ['tab:purple' if is_snode(node) else 'tab:blue' for node in NV]
        # nshape = ['8' if is_head_node(node) else 'o' for node in NV]

        plt.figure(1, figsize=(8, 8))
        plt.axis('on' if plot_box == True else "off")
        plt.title(ax_title)
        if pos is None:
            pos = graphviz_layout(G, prog='neato')

        pos_shadow = copy.deepcopy(pos)
        shift_amount = 0.001
        for k, v in pos_shadow.items():
            x = v[0] + shift_amount
            y = v[1] - shift_amount
            pos_shadow[k] = (x, y)
            # pos_shadow[idx][0] += shift_amount
            # pos_shadow[idx][1] -= shift_amount

        # C = (G.subgraph(c) for c in nx.connected_components(G))
        # for g in C:
        #     c = [random.random()] * nx.number_of_nodes(g)  # random color..
        #     nx.draw(g, pos, node_size=40, node_color=c, vmin=0.0, vmax=1.0, with_labels=False)

        for n in NV:
            g = G.subgraph(n)
            nsz = 60 if is_head_node(n) else 40
            # ncol = 'tab:purple' if is_snode(n) else 'tab:blue'
            # ref: https://matplotlib.org/examples/color/named_colors.html
            # ncol = 'b' if is_snode(n) else 'darkmagenta'
            ncol = 'b' if is_snode(n) else 'teal'
            # marker ref: https://matplotlib.org/api/markers_api.html#module-matplotlib.markers
            nshape = 'D' if is_head_node(n) else 'o'
            nx.draw(g, pos, node_size=nsz, node_color=ncol, node_shape=nshape, with_labels=False)
            nx.draw(g, pos_shadow, node_size=nsz, node_color='k', node_shape=nshape, alpha=0.2)

        nx.draw_networkx_edges(G, pos, edgelist=EDV)
        # nx.draw(G, pos, node_size=nsz, node_color=ncol, node_shape=nshape, vmin=0.0, vmax=1.0, with_labels=False)
        plt.show()

    @classmethod
    def draw_graph(cls, G, en_graphs=None, doc=None,
                   hnode_sz=1200, anode_sz=700,
                   hnode_col='tab:blue', anode_col='tab:red',
                   font_size=12,
                   show_edge_labels=True,
                   plot_box=False,
                   save_file_path=None,
                   ax_title=None,
                   debug=False):

        ### Nodes
        NDV = G.nodes(data=True)
        NV = G.nodes(data=False)
        _is_head_node = lambda x: 'obj' in x
        _is_attr_node = lambda x: 'obj' not in x
        head_nodes = list(filter(_is_head_node, NV))
        attr_nodes = list(filter(_is_attr_node, NV))
        assert len(NDV) == len(head_nodes) + len(attr_nodes)

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
        edge_labels = {}  # edge_labels = {(u, v): d for u, v, d in G.edges(data=True)}
        # edge_labels = [{(u,v): d for u,v,d in G.edges(data=True)}]
        for u, v, d in G.edges(data=True):
            edge_labels.update({(u, v): d})
            # edge_labels.update(d)

        # for k, v in en_graphs.items():
        #     edge_labels.update(v['edge_labels'])

        # Extract relations if doc is passed to the function
        # This will be used instead of <R[NUMBER]>
        # relations = cls.extract_spatial_relations(doc)
        relations = None  # should be already added before reaching draw (in parse)
        # Check that the number of relations is 1 less than the number of nodes, if relations is not None
        assert relations is None or len(relations) == len(head_nodes) - 1

        #### Add <R>, <R2> etc. edge between nodes
        # head_nodes = []
        # for i, node in enumerate(list(G.nodes(data=False))):
        #     if 'obj' in node:
        #         head_nodes.append(node)
        # print(f"head_nodes = {head_nodes}")

        edgelist = G.edges(data=True)

        ## Draw ##

        # Render (MatPlotlib)
        plt.axis('on' if plot_box == True else "off")
        # fig, axs = plt.subplots(1, 2)
        # axs[1].set_title(f"{doc}")
        fig, ax = plt.subplots(1, 1)
        ax.set_title(ax_title, wrap=True)

        nx.draw_networkx_nodes(G, pos, node_size=nsz, node_color=nc)
        nx.draw_networkx_nodes(G, pos_shadow, node_size=nsz, node_color='k', alpha=0.2)

        nx.draw_networkx_edges(G, pos, edgelist=edgelist)
        nx.draw_networkx_labels(G, pos, labels=labels, font_size=font_size, font_color='k', font_family='sans-serif')
        if show_edge_labels:
            nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, label_pos=0.5, font_size=8)

        if save_file_path is not None:
            plt.savefig(save_file_path)
        # if pygraphviz_enabled:
        #   nx.write_dot(G, 'file.dot')
        # See: https://stackoverflow.com/questions/37920935/matplotlib-cant-find-font
        # for findfont errors

        plt.show()

        return G

    @classmethod
    def plot_graph_graphviz(cls, G):
        try:
            import random
            from networkx.drawing.nx_agraph import graphviz_layout
            from networkx.algorithms.isomorphism.isomorph import (
                graph_could_be_isomorphic as isomorphic,
            )
            from networkx.generators.atlas import graph_atlas_g
        except ImportError as ie:
            logger.error(f"Install pygraphviz and graphviz: {ie}")

        # print(f"graph has {nx.number_of_nodes(G)} nodes with {nx.number_of_edges(G)} edges")
        # print(nx.number_strongly_connected_components(G), "connected components")

        plt.figure(1, figsize=(8, 8))
        # layout graphs with positions using graphviz neato
        pos = graphviz_layout(G, prog="neato")
        # color nodes the same in each connected subgraph
        C = (G.subgraph(c) for c in nx.strongly_connected_components(G))
        for g in C:
            c = [random.random()] * nx.number_of_nodes(g)  # random color...
            nx.draw(g, pos, node_size=40, node_color=c, vmin=0.0, vmax=1.0, with_labels=False)
        plt.show()

    @classmethod
    def plot_graph(cls, G, nodelist, labels, edgelist, edge_labels, nsz, nc, font_size=12, show_edge_labels=True):
        pos = nx.spring_layout(G)
        nx.draw_networkx_nodes(G, pos, node_size=nsz, node_color=nc)
        nx.draw_networkx_edges(G, pos, edgelist=edgelist)
        nx.draw_networkx_labels(G, pos, labels=labels, font_size=font_size, font_color='k', font_family='sans-serif')
        if show_edge_labels:
            nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, label_pos=0.5, font_size=8)

    @classmethod
    def plot_entity_graph_dict(cls, entity_graph, font_size=12, show_edge_labels=True):
        en_graph_vals = ['graph', 'nodelist', 'labels', 'edgelist', 'edge_labels', 'nsz', 'nc']
        G, nodelist, labels, edgelist, edge_labels, nsz, nc = list(map(lambda x: entity_graph[x], en_graph_vals))

        pos = nx.spring_layout(G)
        nx.draw_networkx_nodes(G, pos, node_size=nsz, node_color=nc)
        nx.draw_networkx_edges(G, pos, edgelist=edgelist)
        nx.draw_networkx_labels(G, pos, labels=labels, font_size=font_size, font_color='k', font_family='sans-serif')
        if show_edge_labels:
            nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, label_pos=0.5, font_size=8)

    @classmethod
    def visualize(cls, doc, dep=False, save_svg_fn=None):
        try:
            import sys
            from spacy import displacy
            from pathlib import Path

            is_notebook = 'ipykernel' in sys.modules
            colors = {"CLEVR_OBJ": "linear-gradient(90deg, #aa9cfc, #fc9ce7)",
                      "CLEVR_OBJS": "linear-gradient(90deg, #aa9cfc, #fc9ce7)",
                      "SPATIAL_RE": "linear-gradient(90deg, #00ad85bf, #0085ade3)",
                      "MATCHING_RE": "linear-gradient(90deg, #fa8072, #fa80a6)"}
            options = {"ents": ["CLEVR_OBJ", "CLEVR_OBJS",
                                "SPATIAL_RE",
                                "MATCHING_RE"], "colors": colors}

            if is_notebook:
                displacy.render(doc, style='ent', jupyter=True, options=options)
                if dep:
                    displacy.render(doc, style='dep', jupyter=True, options={'distance': 70})
            else:
                displacy.serve(doc, style='ent', options=options.update({'compact': True}))
                if dep:
                    displacy.serve(doc, style='dep', options={'compact': True})

            if save_svg_fn:
                svg = displacy.render(doc, style="dep", jupyter=False)
                output_path = Path(f"../../demo/imgs/{save_svg_fn}")
                output_path.open("w", encoding="utf-8").write(svg)

        except ImportError as ie:
            logger.error("Could not import displacy for visualization")

    @staticmethod
    def __locate_noun(chunks, i):
        for j, c in enumerate(chunks):
            if c.start <= i < c.end:
                return j
        return None

    @classmethod
    def filter_clevr_objs(cls, ents: Tuple) -> Tuple:
        return cls.filter_ents_by_labels(ents, ['CLEVR_OBJ', 'CLEVR_OBJS'])

    @classmethod
    def filter_spatial_re(cls, ents: Tuple) -> Tuple:
        return cls.filter_ents_by_labels(ents, ['SPATIAL_RE'])

    @classmethod
    def filter_matching_re(cls, ents: Tuple) -> Tuple:
        return cls.filter_ents_by_labels(ents, ['MATCHING_RE'])

    @classmethod
    def filter_ents_by_labels(cls, ents: Tuple, labels: List) -> Tuple:
        fn = lambda y, z: tuple(filter(lambda x: x.label_ in z, y))
        return fn(ents, labels)

    @classmethod
    def extract_matching_relations(cls, doc):
        if doc is None:
            return None
        else:
            # Load the matching relations from a file
            # relation_file = os.path.join(os.path.dirname(__file__), '../_data/relation-attrs.txt')
            # matching_relations = set(line.strip() for line in open(relation_file))
            matching_relations = ['size', 'color', 'material', 'shape']
            matching_ents = cls.filter_matching_re(doc.ents)
            # Store the relations
            extracted_relations = []
            for span in matching_ents:
                for t in span:
                    if t.text in matching_relations:
                        extracted_relations.append(t)

            return extracted_relations

    @classmethod
    def extract_spatial_relations(cls, doc):
        '''
        Takes a SpaCy parsed sentence and extracts the spatial relations
        from it. Used in draw_graph to substitute relation name

        Arguments:
            doc: SpaCy parsed sentence

        Returns:
            extracted_relations: list of spatial relations in the parsed sentence
        '''
        if doc is None:
            return None
        else:
            # Load the spatial relations from a file
            relation_file = os.path.join(os.path.dirname(__file__), '../_data/relation-attrs.txt')
            spatial_relations = set(line.strip() for line in open(relation_file))
            spatial_ents = cls.filter_spatial_re(doc.ents)

            # Store the relations
            extracted_relations = []
            for span in spatial_ents:
                for t in span:
                    if t.text in spatial_relations:
                        extracted_relations.append(t)

            return extracted_relations

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

    @classmethod
    def draw_graph_testing(cls, G, en_graphs=None, doc=None,
                   hnode_sz=2000, anode_sz=2000,
                   hnode_col='tab:blue', anode_col='tab:red',
                   font_size=14, attr_font_size=10,
                   figsize=(11,9),
                   show_edge_labels=True,
                   show_edge_attributes=False,
                   layout='graphviz',
                   plot_box=False,
                   save_file_path=None,
                   ax_title=None,
                   debug=False):

        ### Nodes
        NDV = G.nodes(data=True)
        NV = G.nodes(data=False)
        _is_head_node = lambda x: 'obj' in x
        _is_attr_node = lambda x: 'obj' not in x
        head_nodes = list(filter(_is_head_node, NV))
        attr_nodes = list(filter(_is_attr_node, NV))
        assert len(NDV) == len(head_nodes) + len(attr_nodes)

        if layout == 'graphviz':
            from networkx.drawing.nx_agraph import graphviz_layout
            pos = graphviz_layout(G, prog='neato')

            pos_shadow = copy.deepcopy(pos)
            shift_amount = 0.001
            for k, v in pos_shadow.items():
                x = v[0] + shift_amount
                y = v[1] - shift_amount
                pos_shadow[k] = (x, y)
        else:
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
        fig, ax = plt.subplots(1, 1, figsize=figsize)
        ax.set_title(ax_title, wrap=True)

        nx.draw_networkx_nodes(G, pos, node_size=nsz, node_color=nc)
        nx.draw_networkx_nodes(G, pos_shadow, node_size=nsz, node_color='k', alpha=0.2)

        nx.draw_networkx_edges(G, pos, edgelist=edgelist)

        # Draw node labels (different font sizes for head and attribute nodes)
        pos_head, pos_attr = {k: v for k, v in pos.items() if k in head_nodes},\
                             {k: v for k, v in pos.items() if k in attr_nodes}
        labels_head, labels_attr = {k: v for k, v in labels.items() if k in head_nodes},\
                                   {k: v for k, v in labels.items() if k in attr_nodes}     
        nx.draw_networkx_labels(G, pos_head, labels=labels_head, font_size=font_size, font_color='k', font_family='sans-serif')
        nx.draw_networkx_labels(G, pos_attr, labels=labels_attr, font_size=attr_font_size, font_color='k', font_family='sans-serif')
        
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
    def draw_graphviz_testing(cls, G, pos=None, plot_box=False, ax_title=None):
        import random
        from networkx.drawing.nx_agraph import graphviz_layout

        NDV = G.nodes(data=True)
        NV = G.nodes(data=False)
        print(NDV)
        print(NV)
        EV = G.edges(data=False)
        EDV = G.edges(data=True)

        is_head_node = lambda x: 'obj' in x
        is_snode = lambda x: 'Gs' in x
        is_tnode = lambda x: 'Gt' in x

        # Desiderata:
        # Draw the head_nodes a little larger, node_size=60 for hnodes, and 40 for anodes
        # Color the Gs, Gt nodes differently or shape (node_shape)

        # nsz = [60 if is_head_node(node) else 40 for node in NV]
        # ncol = ['tab:purple' if is_snode(node) else 'tab:blue' for node in NV]
        # nshape = ['8' if is_head_node(node) else 'o' for node in NV]

        plt.figure(1, figsize=(8, 8))
        plt.axis('on' if plot_box == True else "off")
        plt.title(ax_title)
        if pos is None:
            pos = graphviz_layout(G, prog='neato')

        pos_shadow = copy.deepcopy(pos)
        shift_amount = 0.001
        for k, v in pos_shadow.items():
            x = v[0] + shift_amount
            y = v[1] - shift_amount
            pos_shadow[k] = (x, y)
            # pos_shadow[idx][0] += shift_amount
            # pos_shadow[idx][1] -= shift_amount

        # C = (G.subgraph(c) for c in nx.connected_components(G))
        # for g in C:
        #     c = [random.random()] * nx.number_of_nodes(g)  # random color..
        #     nx.draw(g, pos, node_size=40, node_color=c, vmin=0.0, vmax=1.0, with_labels=False)

        for n in NV:
            g = G.subgraph(n)
            nsz = 1200 if is_head_node(n) else 700
            # ncol = 'tab:purple' if is_snode(n) else 'tab:blue'
            # ref: https://matplotlib.org/examples/color/named_colors.html
            # ncol = 'b' if is_snode(n) else 'darkmagenta'
            ncol = 'b' if is_snode(n) else 'teal'
            # marker ref: https://matplotlib.org/api/markers_api.html#module-matplotlib.markers
            nshape = 'D' if is_head_node(n) else 'o'
            nx.draw(g, pos, node_size=nsz, node_color=ncol, node_shape=nshape, with_labels=True)
            nx.draw(g, pos_shadow, node_size=nsz, node_color='k', node_shape=nshape, alpha=0.2)

        nx.draw_networkx_edges(G, pos, edgelist=EDV)
        # nx.draw(G, pos, node_size=nsz, node_color=ncol, node_shape=nshape, vmin=0.0, vmax=1.0, with_labels=False)
        plt.show()

        return G
