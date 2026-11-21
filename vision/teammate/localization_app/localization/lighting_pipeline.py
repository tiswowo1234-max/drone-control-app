"""Share illumination preparation between live localization and experiments."""

import argparse
from dataclasses import dataclass

import cv2
import numpy as np

from .illumination_correction import (
    ILLUMINATION_METHODS, NO_PAPER, IlluminationConfig, IlluminationCorrector,
    estimate_illumination, sample_paper,
)
from .shadow_detection import ShadowObservation, detect_shadows


@dataclass(frozen=True)
class LightingObservation:
    image: np.ndarray
    shadow: ShadowObservation | None
    confidence: np.ndarray | None


class LightingProcessor:
    def __init__(self, config=IlluminationConfig(), *, shadow_aware=False, debug=False):
        self.corrector = IlluminationCorrector(config)
        self.shadow_aware = shadow_aware
        self.debug = debug

    def apply(self, frame):
        method = self.corrector.config.method
        detect = self.shadow_aware or self.debug
        paper = sample_paper(frame) if detect or method in ("constancy", "combined", "field") else None
        field = estimate_illumination(paper) if detect or method in ("combined", "field") else None
        prepared_paper = NO_PAPER if paper is None else paper
        image = self.corrector.apply(frame, paper=prepared_paper, field=field)
        shadow = detect_shadows(frame, paper=prepared_paper, field=field) if detect else None
        return LightingObservation(image, shadow, shadow.confidence_mask if self.shadow_aware else None)


def add_lighting_arguments(parser):
    parser.add_argument("--illumination-correction", action=argparse.BooleanOptionalAction,
                        default=None, help="enable the combined illumination correction")
    parser.add_argument("--illumination-method", choices=ILLUMINATION_METHODS,
                        help="correction method; selecting a method also enables it")
    parser.add_argument("--clahe-clip-limit", type=float, default=2)
    parser.add_argument("--clahe-grid", type=int, nargs=2, default=(8, 8), metavar=("X", "Y"))
    parser.add_argument("--shadow-aware", action=argparse.BooleanOptionalAction, default=False,
                        help="use pixel confidence when scoring map matches")
    parser.add_argument("--shadow-debug", action="store_true",
                        help="compute shadow evidence and show or save diagnostic views")


def configured_lighting(args):
    method = args.illumination_method or ("combined" if args.illumination_correction else "none")
    if args.illumination_correction is False:
        method = "none"
    config = IlluminationConfig(method, args.clahe_clip_limit, tuple(args.clahe_grid))
    return LightingProcessor(config, shadow_aware=args.shadow_aware, debug=args.shadow_debug)


def diagnostic_panel(original, observation, detector, palette, *, labels=None):
    """Return a BGR panel with four synchronized views of one input frame."""
    if labels is None:
        labels = detector.detect(observation.image, include_contours=False).labels
    colors = np.asarray(((0, 0, 0), *palette), np.uint8)
    confidence = np.rint(observation.shadow.confidence_mask * 255).astype(np.uint8)
    confidence = cv2.cvtColor(confidence, cv2.COLOR_GRAY2BGR)
    confidence[observation.shadow.shadow_mask, 2] = 255
    panels = [original.copy(), observation.image.copy(), colors[labels], confidence]
    for panel, label in zip(panels, ("Original", "Corrected", "HSV labels", "Confidence / shadow")):
        cv2.putText(panel, label, (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(panel, label, (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack((np.hstack(panels[:2]), np.hstack(panels[2:])))
