"""Grouped, larger-tile Triton candidate for KernelBench L1/P1 on SM90."""

import torch
import triton
import triton.language as tl


@triton.jit
def matmul_kernel(
    a_ptr,
    b_ptr,
    c_ptr,
    m,
    n,
    k,
    stride_am,
    stride_ak,
    stride_bk,
    stride_bn,
    stride_cm,
    stride_cn,
    BLOCK_M: tl.constexpr,
    BLOCK_N: tl.constexpr,
    BLOCK_K: tl.constexpr,
    GROUP_M: tl.constexpr,
):
    pid = tl.program_id(0)
    num_pid_m = tl.cdiv(m, BLOCK_M)
    num_pid_n = tl.cdiv(n, BLOCK_N)
    pids_per_group = GROUP_M * num_pid_n
    group = pid // pids_per_group
    first_pid_m = group * GROUP_M
    group_m = min(num_pid_m - first_pid_m, GROUP_M)
    pid_in_group = pid % pids_per_group
    pid_m = first_pid_m + (pid_in_group % group_m)
    pid_n = pid_in_group // group_m

    offs_m = pid_m * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_n = pid_n * BLOCK_N + tl.arange(0, BLOCK_N)
    offs_k = tl.arange(0, BLOCK_K)
    a_ptrs = a_ptr + offs_m[:, None] * stride_am + offs_k[None, :] * stride_ak
    b_ptrs = b_ptr + offs_k[:, None] * stride_bk + offs_n[None, :] * stride_bn
    accumulator = tl.zeros((BLOCK_M, BLOCK_N), dtype=tl.float32)

    for offset_k in range(0, k, BLOCK_K):
        a = tl.load(
            a_ptrs,
            mask=(offs_m[:, None] < m) & (offs_k[None, :] + offset_k < k),
            other=0.0,
        )
        b = tl.load(
            b_ptrs,
            mask=(offs_k[:, None] + offset_k < k) & (offs_n[None, :] < n),
            other=0.0,
        )
        accumulator = tl.dot(a, b, accumulator)
        a_ptrs += BLOCK_K * stride_ak
        b_ptrs += BLOCK_K * stride_bk

    c_ptrs = c_ptr + offs_m[:, None] * stride_cm + offs_n[None, :] * stride_cn
    tl.store(c_ptrs, accumulator, mask=(offs_m[:, None] < m) & (offs_n[None, :] < n))


def kernel_fn(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    assert a.is_cuda and b.is_cuda
    assert a.ndim == 2 and b.ndim == 2 and a.shape[1] == b.shape[0]
    m, k = a.shape
    _, n = b.shape
    output = torch.empty((m, n), device=a.device, dtype=a.dtype)
    block_m = 128
    block_n = 128
    block_k = 64
    grid = (triton.cdiv(m, block_m) * triton.cdiv(n, block_n),)
    matmul_kernel[grid](
        a,
        b,
        output,
        m,
        n,
        k,
        a.stride(0),
        a.stride(1),
        b.stride(0),
        b.stride(1),
        output.stride(0),
        output.stride(1),
        BLOCK_M=block_m,
        BLOCK_N=block_n,
        BLOCK_K=block_k,
        GROUP_M=8,
        num_warps=8,
        # Three 64 KiB A/B stages fit below H100's per-block shared-memory cap.
        num_stages=3,
    )
    return output
