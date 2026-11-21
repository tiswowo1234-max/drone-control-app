"""Select one same-frame correction under unknown map/tag error correlation."""

from dataclasses import dataclass, field, replace
import math

import numpy as np


@dataclass(frozen=True)
class LocalizationObservation:
    source: str
    frame_id: int
    timestamp: float
    x_m: float | None = None
    y_m: float | None = None
    yaw_deg: float | None = None
    span_m: float | None = None
    covariance: tuple | None = None  # 2x2 xy covariance in model m²
    yaw_sigma_deg: float | None = None
    quality_metrics: dict = field(default_factory=dict)
    accepted: bool = True
    rejection_reason: str | None = None

    @property
    def xy(self):
        return self.x_m, self.y_m

    @property
    def approximate(self):
        return self.quality_metrics.get("pose_model") == "planar"

    def reject(self, reason):
        return replace(self, accepted=False, rejection_reason=reason)


def yaw_difference(a, b):
    return (a - b + 180) % 360 - 180


def from_map(estimate, frame_id, timestamp, sigma_m, yaw_sigma_deg):
    obs = LocalizationObservation(
        "MAP", frame_id, timestamp, estimate.x_m, estimate.y_m, estimate.yaw_deg, estimate.span_m,
        ((sigma_m ** 2, 0.), (0., sigma_m ** 2)), yaw_sigma_deg,
        dict(iou=estimate.score, margin=estimate.margin, map_source=estimate.observation_source),
    )
    return obs if estimate.success else obs.reject(estimate.reason)


def observations_agree(map_obs, tag_obs):
    # The sum of marginal standard deviations bounds the residual under unknown correlation.
    residual = np.subtract(map_obs.xy, tag_obs.xy)
    distance = np.linalg.norm(residual)
    direction = residual / distance if distance else np.array([1., 0.])
    position_limit = sum(math.sqrt(direction @ np.asarray(o.covariance) @ direction) for o in (map_obs, tag_obs))
    turn = abs(yaw_difference(tag_obs.yaw_deg, map_obs.yaw_deg))
    yaw_limit = 2 * (map_obs.yaw_sigma_deg + tag_obs.yaw_sigma_deg)
    return distance <= min(0.20, 2 * position_limit) and turn <= min(45., yaw_limit)


def select_observation(map_obs, tag_obs, motion_gate, *, confirmed_tag_recovery=False):
    """Return the selected correction, input statuses, and decision for this frame."""
    if map_obs is not None and tag_obs is not None and (
            (map_obs.frame_id, map_obs.timestamp) != (tag_obs.frame_id, tag_obs.timestamp)):
        tag_obs = tag_obs.reject("tag observation belongs to another frame")
    if map_obs is not None and map_obs.accepted and not motion_gate(map_obs):
        map_obs = map_obs.reject("map observation rejected by motion gate")
    if tag_obs is not None and tag_obs.accepted and not motion_gate(tag_obs):
        tag_obs = tag_obs.reject("tag observation rejected by motion gate")
    valid = [o for o in (map_obs, tag_obs) if o is not None and o.accepted]
    if len(valid) < 2:
        if valid:
            selected = valid[0]
            return selected, map_obs, tag_obs, f"{selected.source} accepted"
        rejected = [o.rejection_reason for o in (map_obs, tag_obs) if o is not None and o.rejection_reason]
        return None, map_obs, tag_obs, " / ".join(rejected) or "no observation"

    if not observations_agree(map_obs, tag_obs):
        reason = "map/tag position or yaw conflict"
        if tag_obs.approximate:
            return map_obs, map_obs, tag_obs.reject(reason), "MAP accepted / " + reason
        if confirmed_tag_recovery or len(tag_obs.quality_metrics.get("used_ids", ())) >= 2:
            return tag_obs, map_obs.reject(reason), tag_obs, "APRILTAG consensus accepted / " + reason
        return None, map_obs.reject(reason), tag_obs.reject(reason), reason

    # Covariance intersection uses a fixed convex weight and never counts these
    # two correlated estimates as independent Kalman corrections.
    a, b = [0.5 * np.linalg.inv(o.covariance) for o in valid]
    covariance = np.linalg.inv(a + b)
    x, y = covariance @ (a @ map_obs.xy + b @ tag_obs.xy)
    # Off-diagonal covariance can move the fused mean outside both scalar ranges.
    # Apply the same prediction gate to the resulting correction below.
    yaw_weights = np.array([0.5 / o.yaw_sigma_deg ** 2 for o in valid])
    turn = yaw_difference(tag_obs.yaw_deg, map_obs.yaw_deg)
    yaw = (map_obs.yaw_deg + turn * yaw_weights[1] / yaw_weights.sum()) % 360
    if tag_obs.approximate and map_obs.span_m is not None:
        span = map_obs.span_m
    else:
        span = tag_obs.span_m if tag_obs.span_m is not None else map_obs.span_m
    fused = LocalizationObservation("FUSION", tag_obs.frame_id, tag_obs.timestamp, float(x), float(y), yaw,
                                    span, tuple(map(tuple, covariance)), float(yaw_weights.sum() ** -0.5))
    if not motion_gate(fused):
        reason = "fused observation rejected by motion gate"
        return None, map_obs.reject(reason), tag_obs.reject(reason), reason
    return fused, map_obs, tag_obs, "FUSION accepted"
