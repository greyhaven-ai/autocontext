# Reproduction source and attribution

The pinned contract describes Experiment 8 of **Sparks of Weight-Space Programming: LLMs can natively read & write neural network weights**, by Rikiya Takehi, Jayden Teoh, Alex Zhang, and Omar Khattab (2026).

- [Research article](https://rikiyat.github.io/blog/weight-space-programming/)
- [Pinned source release](https://github.com/RikiyaT/lm_understand-logs/tree/4d09ba6d6a7e891dd8063974dfe81b3d78e3494f)
- [Experiment description](https://github.com/RikiyaT/lm_understand-logs/blob/4d09ba6d6a7e891dd8063974dfe81b3d78e3494f/exp8_math/README.md)
- [Upstream license](https://github.com/RikiyaT/lm_understand-logs/blob/4d09ba6d6a7e891dd8063974dfe81b3d78e3494f/LICENSE)

The authors license their scripts under MIT and their authored logs, data, and networks under CC BY 4.0. Third-party model and benchmark content retains its own terms. The lock records metadata and verification hashes; it does not redistribute a checkpoint, benchmark dataset, or upstream program. Offline fixtures are synthetic examples authored for autocontext, not benchmark questions. Downloading a pinned source program does not execute it.

The math extraction and IFEval adapter contracts follow the pinned `exp8_math/code/mathqa.py` and `exp8_math/code/evaluate.py`. Autocontext adds explicit outcome and infrastructure-error handling and injectable offline checkers. An injected checker fixture is not a replacement for the complete official benchmark implementation.

The lock's required assets were decoded from GitHub's base64 content responses, checked against each response's Git blob identity, and hashed as original bytes with SHA-256. The binary patch was checked as bytes without importing or executing any downloaded code. Its SHA-256 is `e92a92aafd1f1068b4fd94aa0fbe081ba3ddea2047ee7bcac7161d861be299e7` (387,619 bytes).

The optional base-model shard and tokenizer vocabulary hashes/sizes come from the LFS metadata of [Qwen/Qwen3.5-0.8B at revision 2fc06364715b967f1860aea9cf38778875588b17](https://huggingface.co/Qwen/Qwen3.5-0.8B/tree/2fc06364715b967f1860aea9cf38778875588b17). Those two files were not downloaded while preparing this milestone. The released text-only config and tokenizer config are pinned separately from the full public model; they must not be replaced with the full multimodal defaults.

## Upstream script license

MIT License

Copyright (c) 2026 the authors of "LLMs can understand and write neural network weights"

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
