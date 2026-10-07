---
name: camera-motion-math
description: 6-DoF Bezier trajectory generation, Plücker coordinate ray embeddings, Homography 2D planar warps, and Depth-Anything-V2 3D parallax reprojection.
---

# Camera Motion Math Skill

## 1. 6-DoF Trajectory Spline Formulation:
- Camera Extrinsics $[R|T]$ at frame $t \in [0, T]$:
  * Rotation: $R(t) = \text{Euler2Mat}(\text{Yaw}(t), \text{Pitch}(t), \text{Roll}(t))$
  * Translation: $T(t) = T_{\text{target}} + \text{OrbitOffset}(t) + R(t) \cdot [\text{Truck}(t), \text{Pedestal}(t), \text{Dolly}(t)]^T$
- Continuous Bezier Spline: Smooth cubic interpolation between user keyframes.

## 2. Plücker Coordinate Ray Calculation:
For pixel $(u, v)$ with intrinsic matrix $K$:
- Direction vector: $d(u, v) = R \cdot K^{-1} [u, v, 1]^T / \|R \cdot K^{-1} [u, v, 1]^T\|_2$
- Moment vector: $m(u, v) = C \times d(u, v)$ (where $C = -R^T T$)
- Export format: `*_camera.npz` containing $(d, m)$ tensor of shape $(B, F, H, W, 6)$.

## 3. Depth Parallax Reprojection:
- Unproject pixel $p_0 = (u_0, v_0)$ using depth $Z_0(u_0, v_0)$: $P_{\text{world}} = R_0^T (K^{-1} [u_0, v_0, 1]^T Z_0 - T_0)$
- Reproject to frame $t$: $p_t \sim K (R_t P_{\text{world}} + T_t)$
- Uncovered/Occluded pixels: Mark confidence mask = $1.0$ (generate/inpaint) and fill with gray placeholder.

## 4. Keyframe VAE Temporal Snapping:
- Lock keyframes to index $4m + 1$ (e.g. 1, 5, 9, 13, ..., 81) due to 4x VAE temporal downsampling.
- Apply FreeNoise ($\beta = 0.25$, window 4) and Slerp at boundaries.
