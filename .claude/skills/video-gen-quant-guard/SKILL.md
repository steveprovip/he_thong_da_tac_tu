---
name: video-gen-quant-guard
description: Memory and VRAM guardrail for DiT neural video synthesis on RTX 5060 Ti 16GB, preventing Windows 11 shared sysmem spillage and enforcing 4-phase sequential execution.
---

# Video Gen Quant Guard Skill

## Core Principles:
1. **Hard VRAM Ceiling**: Peak VRAM must NEVER exceed 14.5 GB on 16GB cards.
2. **Sequential 4-Phase Isolation**:
   - Phase 1: Text Encoder runs in isolated subprocess, saves tensor embeddings to cache, releases 100% RAM/VRAM.
   - Phase 2: Conditioning prep (motion guide, masks) done on CPU.
   - Phase 3: DiT denoise runs with TorchAO FP8 (`float8_e4m3fn`) or GGUF (Q4_K_M / Q5_K_M).
   - Phase 4: DiT is offloaded; VAE Decoder runs with Spatial Tiling (512x512 with 64px overlap) to prevent VRAM spikes.
3. **Deterministic Purge Function**:
```python
import gc
import torch

def purge_vram():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
```
4. **Environment Variables**:
```bash
set PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True,garbage_collection_threshold:0.8
```
