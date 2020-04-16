#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# File   : demo.py
# Author : Raeid Saqur
# Email  : raeidsaqur@gmail.com
# Date   : 09/22/2019
#
# This file is part of PGFM Parser.
# Distributed under terms of the MIT license.
# https://github.com/raeidsaqur/pgfmParser

"""
A small demo for the scene graph parser.
"""
import os, sys, platform
import json

nb_dir = os.path.split(os.getcwd())[0]
if nb_dir not in sys.path:
    sys.path.insert(0, nb_dir)

print(f"{os.name}.{platform.system()}.{platform.release()}.{platform.node()}")

import clevr_parser
from clevr_parser.utils import *
from tests import TEMPLATES, get_s_sample

from typing import *

def demo_closure(parser):
    q = "Is there a blue thing that is the same size as the brown shiny object in front of the gray matte sphere?"
    _, doc = parser.parse(q)
    parser.visualize(doc, dep=False)


def demo_G_text(parser, text=None):
    text = "Is the gray matte object the same size as the green rubber cylinder" if text is None else text
    q_graph, q_doc = parser.parse(text, return_doc=True)
    ax_title = f"{q_doc}"
    G_text, en_graphs = parser.get_nx_graph_from_doc(q_doc)
    G = parser.draw_graph(G_text, en_graphs, ax_title=ax_title)


def demo_G_scene(parser, gfp):
    from clevr_parser import utils
    groundings = utils.load_groundings_from_path(gfp)
    g = groundings[0]
    G_img = parser.draw_clevr_img_scene_graph(g)

def demo_Gs_spatial_relation(parser, text=None):
    text = "The sphere is behind a rubber cylinder right of a metal cube" if text is None else text
    q_graph, q_doc = parser.parse(text, return_doc=True)
    #parser.visualize(q_doc)
    ax_title = f"{q_doc}"
    G_text, en_graphs = parser.get_nx_graph_from_doc(q_doc)
    G = parser.draw_graph(G_text, en_graphs, ax_title=ax_title, doc=q_doc)


def main():
    clevr_img_name = lambda split, i: f"CLEVR_{split}_{i:06d}.png"
    parser = clevr_parser.Parser(backend='spacy', model='en_core_web_sm',
                                 has_spatial=True).get_backend(identifier='spacy')
    clevrr_baseline_qp = "../data/CLEVRR_v1.0/questions/CLEVRR_compare_baseline_questions.json"
    image_grounding_parsed_gp = "../data/CLEVR_v1.0/scenes_parsed/val_scenes_parsed.json"

    s_ams_bline = get_s_sample(template="and_mat_spa", dist="train")
    demo_Gs_spatial_relation(parser, text=s_ams_bline)
    print("done")


if __name__ == '__main__':
    main()

