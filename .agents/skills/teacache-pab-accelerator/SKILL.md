---
name: teacache-pab-accelerator
description: Temporal Attention Caching (TeaCache) and Pyramid Attention Broadcast (PAB) for Wan2.1 DiT to skip 35-50% redundant block computation.
---

# TeaCache & PAB Acceleration Skill

## 1. Mathematical Formulation:
At diffusion step $t$, compute normalized relative difference between consecutive latents:
$$\delta_t = \frac{\|L_t - L_{t-1}\|_2}{\|L_{t-1}\|_2}$$

## 2. Dynamic Block Skipping Policy:
- If $\delta_t < \tau_{\text{thresh}}$ (e.g. $\tau = 0.12$ in middle diffusion steps):
  * Skip Spatial-Temporal Attention computation in blocks $N_{\text{skip}} \in [4, 28]$.
  * Reuse previous step's modulated output cache: $H_t = H_{t-1} + \Delta_{\text{cached}}$.
- In initial ($t \approx T$) and final ($t \approx 0$) steps: Full computation enabled to preserve high-frequency details.

## 3. Async Double-Buffered Weight Transfer:
```python
# Stream Block N+1 while computing Block N
compute_stream = torch.cuda.Stream()
transfer_stream = torch.cuda.Stream()

with torch.cuda.stream(transfer_stream):
    next_block_gpu = next_block_cpu.to('cuda', non_blocking=True)

with torch.cuda.stream(compute_stream):
    out = current_block_gpu(latents)

compute_stream.wait_stream(transfer_stream)
```
