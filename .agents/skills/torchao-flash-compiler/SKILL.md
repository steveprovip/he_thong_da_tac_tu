---
name: torchao-flash-compiler
description: Low-level FP8/Int4 quantization and Blackwell sm_120 attention kernel optimization for RTX 5060 Ti.
---

# TorchAO & Blackwell Kernel Optimization Skill

## 1. Native FP8 on Blackwell sm_120:
- Use TorchAO `float8_e4m3fn` with dynamic per-channel scaling:
```python
from torchao.quantization import quantize_, float8_dynamic_activation_float8_weight
# Quantize DiT linear modules
quantize_(model, float8_dynamic_activation_float8_weight())
```
- For GGUF loading: Use `llama.cpp` / `gguf` python bindings with memory-mapped (`mmap`) fast NVMe reads.

## 2. Attention Dispatch Hierarchy:
```python
def dispatch_attention(q, k, v, mask=None, is_causal=False):
    # Tier 1: SageAttention >= 2.1.1 (if wheel installed with sm_120 support)
    try:
        from sageattention import sageattn
        return sageattn(q, k, v, is_causal=is_causal)
    except Exception:
        pass
    
    # Tier 2: PyTorch Native SDPA (FlashAttention-2 / CuDNN fused)
    import torch.nn.functional as F
    return F.scaled_dot_product_attention(q, k, v, attn_mask=mask, is_causal=is_causal)
```

## 3. Fused Operators:
- Fused QKV linear projections into a single GEMM call.
- In-place RMSNorm + RoPE tensor rotation.
