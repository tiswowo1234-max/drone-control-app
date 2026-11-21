"""Shared scale arguments for matching, diagnosis and benchmarking."""

import argparse

from .map_matching import SearchConfig


def add_scale_arguments(parser, *, auto_scale=False):
    defaults = SearchConfig()
    parser.add_argument("--auto-scale", action=argparse.BooleanOptionalAction, default=auto_scale,
                        help="search a broad range of map-coordinate ground spans")
    parser.add_argument("--scale-min", type=float, default=defaults.scale_min,
                        help="minimum automatic scale relative to --nominal-span")
    parser.add_argument("--scale-max", type=float, default=defaults.scale_max,
                        help="maximum automatic scale relative to --nominal-span")
    parser.add_argument("--scale-steps", type=int, default=defaults.scale_steps,
                        help="number of logarithmic coarse scale candidates")
    parser.add_argument("--scale-refine-steps", type=int, default=defaults.scale_refine_steps,
                        help="number of fine samples per scale and yaw neighborhood")


def scale_arguments(args):
    return dict(auto_scale=args.auto_scale, scale_min=args.scale_min, scale_max=args.scale_max,
                scale_steps=args.scale_steps, scale_refine_steps=args.scale_refine_steps)
