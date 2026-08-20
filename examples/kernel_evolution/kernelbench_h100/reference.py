"""KernelBench v0.1 Level 1, problem 1: square matrix multiplication.

Source: https://github.com/ScalingIntelligence/KernelBench/blob/main/
KernelBench/level1/1_Square_matrix_multiplication_.py
"""

import torch
import torch.nn as nn


class Model(nn.Module):
    """Compute a single square matrix multiplication."""

    def __init__(self):
        super().__init__()

    def forward(self, a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
        return torch.matmul(a, b)


N = 2048 * 2


def get_inputs():
    a = torch.rand(N, N)
    b = torch.rand(N, N)
    return [a, b]


def get_init_inputs():
    return []
